import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.security.KeyStore;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * Extracts Philips HomeID app credentials from the app's local storage.
 *
 * Tries multiple extraction methods in order:
 * 1. SQLite database (network_node.db) - used by older firmwares
 * 2. EncryptedSharedPreferences (COMMUNICATION_LIB_PREFERENCES) - newer firmwares
 * 3. Core SecurePreferences (ONE_KA_ENCRYPTED_PREFERENCES) - with XOR layer
 * 4. AES-CBC SecurePreferences (COMMUNICATION_LIB_PREFERENCES) - fallback path
 *
 * Must be run on a rooted Android device as the Philips app's UID via app_process.
 * The Philips APK's classes are loaded via createPackageContext(CONTEXT_INCLUDE_CODE).
 *
 * Usage: CLASSPATH=extractor.dex app_process / ExtractCreds
 */
public class ExtractCreds {

    private static final String PKG = "com.philips.ka.oneka.app";
    private static final String STORAGE_PREFS_CLASS =
            "com.philips.ka.oneka.communication.library.storage.StoragePreferences";
    private static final String SECURE_PREFS_CLASS =
            "com.philips.ka.oneka.core.android.SecurePreferences";
    private static final String COMM_SECURE_PREFS_CLASS =
            "com.philips.ka.oneka.communication.library.storage.SecurePreferences";
    private static final String COMM_LIB_PACKAGE =
            "com.philips.ka.oneka.communication.library";

    private static final String[] CREDENTIAL_SUFFIXES = {
        "DEVICE_CLIENT_ID", "DEVICE_CLIENT_SECRET",
        "DEVICE_HSDP_ID", "DEVICE_CPP_ID"
    };

    // Saved for deferred Application setup (after SQLite, before Tink)
    private static Object sThread;
    private static Class<?> sThreadClass;

    // MAC addresses discovered from SQLite/preferences for use in key lookups
    private static Set<String> discoveredMacs = new LinkedHashSet<>();

    // When true, dump all entries from encrypted stores (not just known keys)
    private static boolean dumpAll = false;

    public static void main(String[] args) {
        System.out.println("Philips HomeID Credential Extractor");
        System.out.println("====================================");
        System.out.println();
        System.out.flush();

        // Parse arguments: [--dump-all] [MAC_ADDRESS]
        for (String arg : args) {
            if (arg == null || arg.isEmpty()) continue;
            if ("--dump-all".equals(arg)) {
                dumpAll = true;
                System.out.println("Mode: dump all entries");
            } else {
                discoveredMacs.add(arg);
                System.out.println("Using MAC: " + arg);
            }
        }
        if (args.length > 0) System.out.println();

        try {
            bypassHiddenApiRestrictions();

            Context context = getAppContext();
            System.out.flush();

            ClassLoader apkLoader = context.getClassLoader();
            System.out.println();

            // Method 1: SQLite database (older firmwares)
            // Done BEFORE Application setup — SQLiteDatabase triggers
            // Settings provider access which fails if Application is set
            System.out.println("--- Method 1: SQLite Database ---");
            dumpSqliteDatabase();

            // Now set up Application + KeyStore for encrypted prefs access.
            // This must happen after SQLite but before any Tink operations.
            setupApplication(sThreadClass, sThread, context);
            registerAndroidKeyStore();

            // Method 2: EncryptedSharedPreferences via StoragePreferences
            System.out.println();
            System.out.println("--- Method 2: Encrypted Preferences ---");
            dumpStoragePreferences(context, apkLoader);

            // Method 3: SecurePreferences with XOR layer
            if (!discoveredMacs.isEmpty()) {
                System.out.println();
                System.out.println("--- Method 3: Secure Preferences ---");
                dumpCoreSecurePreferences(context, apkLoader);
            }

            // Method 4: AES-CBC fallback (no Keystore needed)
            System.out.println();
            System.out.println("--- Method 4: AES-CBC Preferences ---");
            dumpAesCbcPreferences(context, apkLoader);

        } catch (Throwable t) {
            System.err.println("[FAIL] " + t.getClass().getName()
                    + ": " + t.getMessage());
            t.printStackTrace(System.err);
            System.err.println();
            System.err.println("Troubleshooting:");
            System.err.println("  - Are you running as the Philips app's UID?");
            System.err.println("  - Is the Philips HomeID app installed?");
        }
    }

    /**
     * Bypass Android's hidden API restrictions so that reflection on
     * framework-internal fields (ActivityThread.sCurrentActivityThread,
     * mInitialApplication, etc.) actually works. Without this, Android 9+
     * silently blocks setAccessible/set on dark-greylisted fields.
     */
    private static void bypassHiddenApiRestrictions() {
        try {
            Class<?> vmRuntime = Class.forName("dalvik.system.VMRuntime");
            Method getRuntime = vmRuntime.getMethod("getRuntime");
            Object runtime = getRuntime.invoke(null);
            Method setExemptions = vmRuntime.getMethod(
                    "setHiddenApiExemptions", String[].class);
            setExemptions.invoke(runtime, (Object) new String[]{""});
        } catch (Exception e) {
            System.out.println("[WARN] Hidden API bypass failed: "
                    + e.getMessage());
        }
    }

    /**
     * Get an Android Context for the Philips app package.
     */
    private static Context getAppContext() throws Exception {
        try {
            Class.forName("android.os.Looper")
                    .getMethod("prepareMainLooper").invoke(null);
        } catch (Exception e) {
            // Already prepared or not needed
        }

        Class<?> atClass = Class.forName("android.app.ActivityThread");

        // Create ActivityThread without attach() — attach(true) requires
        // system UID, attach(false) does IPC to ActivityManager.
        try {
            Constructor<?> ctor = atClass.getDeclaredConstructor();
            ctor.setAccessible(true);
            Object thread = ctor.newInstance();

            // Set as current ActivityThread (normally done by attach())
            try {
                Field sThreadField = atClass.getDeclaredField(
                        "sCurrentActivityThread");
                sThreadField.setAccessible(true);
                sThreadField.set(null, thread);
            } catch (Exception e) {
                // Will be retried in setupApplication
            }

            Context sysContext =
                    (Context) atClass.getMethod("getSystemContext").invoke(thread);
            Context pkgContext = sysContext.createPackageContext(
                    PKG, Context.CONTEXT_INCLUDE_CODE
                            | Context.CONTEXT_IGNORE_SECURITY);

            sThread = thread;
            sThreadClass = atClass;
            return pkgContext;
        } catch (Exception e) {
            System.out.println("[DEBUG] Method 1 failed: " + e.getMessage());
        }

        // Fallback: systemMain() — may crash on some devices
        try {
            Object thread = atClass.getMethod("systemMain").invoke(null);
            Context sysContext =
                    (Context) atClass.getMethod("getSystemContext").invoke(thread);
            Context pkgContext = sysContext.createPackageContext(
                    PKG, Context.CONTEXT_INCLUDE_CODE
                            | Context.CONTEXT_IGNORE_SECURITY);
            sThread = thread;
            sThreadClass = atClass;
            return pkgContext;
        } catch (Exception e) {
            System.out.println("[DEBUG] Method 2 failed: " + e.getMessage());
        }

        throw new Exception("Could not create Android Context");
    }

    /**
     * Create an Application wrapping the given context and set it as the
     * ActivityThread's initial application. Makes both
     * ActivityThread.currentApplication() and context.getApplicationContext()
     * return non-null, which is required by android.security.KeyStore and
     * Tink's EncryptedSharedPreferences.
     */
    private static void setupApplication(Class<?> atClass, Object thread,
            Context context) {
        try {
            Class<?> appClass = Class.forName("android.app.Application");
            Object app = appClass.getDeclaredConstructor().newInstance();

            // Application extends ContextWrapper — attach our context as base
            Class<?> cwClass = Class.forName("android.content.ContextWrapper");
            Method attachMethod = cwClass.getDeclaredMethod(
                    "attachBaseContext", Context.class);
            attachMethod.setAccessible(true);
            attachMethod.invoke(app, context);

            // Set as the thread's initial application
            Field appField = atClass.getDeclaredField("mInitialApplication");
            appField.setAccessible(true);
            appField.set(thread, app);

            // Also set mApplication on the context's LoadedApk so that
            // context.getApplicationContext() returns non-null
            Field pkgInfoField = context.getClass()
                    .getDeclaredField("mPackageInfo");
            pkgInfoField.setAccessible(true);
            Object loadedApk = pkgInfoField.get(context);
            if (loadedApk != null) {
                Field mAppField = loadedApk.getClass()
                        .getDeclaredField("mApplication");
                mAppField.setAccessible(true);
                mAppField.set(loadedApk, app);
            }
        } catch (Exception e) {
            System.out.println("[WARN] Application setup failed: "
                    + e.getMessage());
        }
    }

    /**
     * Register the AndroidKeyStore security provider.
     * In app_process context the provider is not pre-registered.
     */
    private static void registerAndroidKeyStore() {
        String[] providerClasses = {
            "android.security.keystore2.AndroidKeyStoreProvider",
            "android.security.keystore.AndroidKeyStoreProvider"
        };
        for (String className : providerClasses) {
            try {
                Class<?> cls = Class.forName(className);
                cls.getMethod("install").invoke(null);
                return;
            } catch (ClassNotFoundException e) {
                // Try next
            } catch (Exception e) {
                System.out.println("[WARN] " + className + ": "
                        + e.getMessage());
            }
        }
        System.out.println("[WARN] Could not register AndroidKeyStore");
    }

    /**
     * Read credentials from the SQLite database (network_node.db).
     * Older firmwares store client_id, client_secret, and encryption_key here.
     * Also collects MAC addresses (cppid) for use in other methods.
     */
    private static void dumpSqliteDatabase() {
        String dbPath = "/data/data/" + PKG + "/databases/network_node.db";

        if (!new File(dbPath).exists()) {
            System.out.println("  Database not found");
            return;
        }

        SQLiteDatabase db = null;
        Cursor cursor = null;
        try {
            db = SQLiteDatabase.openDatabase(
                    dbPath, null, SQLiteDatabase.OPEN_READONLY);
            cursor = db.rawQuery("SELECT * FROM network_node", null);

            int colCount = cursor.getColumnCount();
            int rowNum = 0;
            while (cursor.moveToNext()) {
                rowNum++;
                System.out.println("  Row " + rowNum + ":");
                for (int i = 0; i < colCount; i++) {
                    if (cursor.isNull(i)) continue;
                    String colName = cursor.getColumnName(i);
                    String value = cursor.getString(i);
                    if (value != null && !value.isEmpty()) {
                        System.out.println("    " + colName + " = " + value);
                        if ("cppid".equals(colName)) {
                            discoveredMacs.add(value);
                        }
                    }
                }
            }
            if (rowNum == 0) {
                System.out.println("  Database is empty");
            }

        } catch (Exception e) {
            System.out.println("  [FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
        } finally {
            if (cursor != null) {
                try { cursor.close(); } catch (Exception e) { /* ignore */ }
            }
            if (db != null) {
                try { db.close(); } catch (Exception e) { /* ignore */ }
            }
        }
    }

    /**
     * Open COMMUNICATION_LIB_PREFERENCES via the Philips app's StoragePreferences.
     * This class internally handles EncryptedSharedPreferences (Tink) decryption.
     * Also collects MAC addresses from key prefixes for use in method 3.
     */
    private static void dumpStoragePreferences(Context context,
            ClassLoader loader) {
        try {
            Class<?> cls = loader.loadClass(STORAGE_PREFS_CLASS);
            Object instance = cls.getConstructor(Context.class)
                    .newInstance(context);

            SharedPreferences prefs = findPrefsField(cls, instance);
            if (prefs == null) {
                System.out.println("  [FAIL] No SharedPreferences field found");
                return;
            }

            Map<String, ?> all = prefs.getAll();
            int count = 0;
            for (Map.Entry<String, ?> entry : all.entrySet()) {
                String key = entry.getKey();
                if (key.startsWith("__androidx_security")) continue;

                System.out.println("  " + key + " = " + entry.getValue());
                count++;

                // Extract MAC prefix from keys like "e4:bc:96:0f:7d:9dDEVICE_CLIENT_ID"
                for (String suffix : CREDENTIAL_SUFFIXES) {
                    if (key.endsWith(suffix) && key.length() > suffix.length()) {
                        String mac = key.substring(
                                0, key.length() - suffix.length());
                        discoveredMacs.add(mac);
                    }
                }
            }

            if (count > 0) {
                System.out.println("  Found " + count + " entries");
            } else {
                System.out.println("  No entries found");
            }

        } catch (ClassNotFoundException e) {
            System.out.println("  StoragePreferences class not found in APK");
        } catch (Exception e) {
            System.out.println("  [FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
        }
    }

    /**
     * Open ONE_KA_ENCRYPTED_PREFERENCES via the Philips app's SecurePreferences.
     * Uses getString() with discovered MAC addresses to find credentials.
     */
    private static void dumpCoreSecurePreferences(Context context,
            ClassLoader loader) {
        try {
            // Safety check: verify master key exists AND is accessible.
            // The SecurePreferences constructor DELETES the master key and
            // the entire prefs file if EncryptedSharedPreferences fails
            // (e.g. due to SELinux blocking crypto under magisk context).
            try {
                KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
                ks.load(null);
                if (!ks.containsAlias("_androidx_security_master_key_")) {
                    System.out.println("  [SKIP] Master key not in "
                            + "AndroidKeyStore — cannot decrypt safely");
                    dumpRawPrefsCount(context, "ONE_KA_ENCRYPTED_PREFERENCES");
                    return;
                }
                // Actually try to access the key — containsAlias() can
                // succeed even when SELinux blocks real crypto operations.
                KeyStore.Entry entry = ks.getEntry(
                        "_androidx_security_master_key_", null);
                if (entry == null) {
                    System.out.println("  [SKIP] Master key exists but "
                            + "cannot be accessed (SELinux?)");
                    System.out.println("  Try: setenforce 0");
                    dumpRawPrefsCount(context, "ONE_KA_ENCRYPTED_PREFERENCES");
                    return;
                }
            } catch (Exception e) {
                System.out.println("  [SKIP] Cannot access AndroidKeyStore"
                        + " master key: " + e.getMessage());
                System.out.println("  Try: setenforce 0");
                dumpRawPrefsCount(context, "ONE_KA_ENCRYPTED_PREFERENCES");
                return;
            }

            // Verify Tink keysets exist in the prefs file before letting
            // the constructor touch it — if they're missing, there's
            // nothing to decrypt and the constructor would just destroy
            // the file trying to recreate them.
            {
                SharedPreferences raw = context.getSharedPreferences(
                        "ONE_KA_ENCRYPTED_PREFERENCES", 0);
                String keyKeyset = raw.getString(
                        "__androidx_security_crypto_encrypted_prefs_key_keyset__",
                        null);
                String valKeyset = raw.getString(
                        "__androidx_security_crypto_encrypted_prefs_value_keyset__",
                        null);
                if (keyKeyset == null || valKeyset == null) {
                    System.out.println("  [SKIP] Tink keysets missing "
                            + "from preferences file");
                    dumpRawPrefsCount(context, "ONE_KA_ENCRYPTED_PREFERENCES");
                    return;
                }
            }

            Class<?> cls = loader.loadClass(SECURE_PREFS_CLASS);

            Object instance = null;
            for (Constructor<?> ctor : cls.getDeclaredConstructors()) {
                Class<?>[] params = ctor.getParameterTypes();
                if (params.length == 3
                        && params[0].isAssignableFrom(context.getClass())
                        && params[1] == String.class) {
                    ctor.setAccessible(true);
                    Object stringProvider = createStringProviderProxy(params[2]);
                    instance = ctor.newInstance(context, PKG, stringProvider);
                    break;
                }
            }

            if (instance == null) {
                System.out.println("  Could not instantiate SecurePreferences");
                return;
            }

            Method getStringMethod = findGetStringMethod(cls);
            if (getStringMethod != null) {
                tryKnownKeys(instance, getStringMethod);
            }

            // Dump entries from the underlying EncryptedSharedPreferences
            SharedPreferences underlying = findPrefsField(cls, instance);
            if (underlying != null) {
                try {
                    Map<String, ?> all = underlying.getAll();
                    int count = 0;
                    for (Map.Entry<String, ?> entry : all.entrySet()) {
                        String key = entry.getKey();
                        if (key.startsWith("__androidx_security")) continue;
                        count++;
                        if (dumpAll) {
                            System.out.println("  [raw] " + key + " = "
                                    + entry.getValue());
                        }
                    }
                    if (dumpAll) {
                        System.out.println("  " + count
                                + " total entries in encrypted store");
                    } else {
                        System.out.println("  (" + count
                                + " total entries in encrypted store)");
                    }
                } catch (Exception e) {
                    System.out.println("  [WARN] Cannot enumerate entries: "
                            + e.getClass().getSimpleName());
                }
            }

        } catch (ClassNotFoundException e) {
            System.out.println("  SecurePreferences class not found in APK");
        } catch (Exception e) {
            System.out.println("  [FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
        }
    }

    /**
     * Show how many entries a raw SharedPreferences file has (for diagnostics).
     */
    private static void dumpRawPrefsCount(Context context, String name) {
        try {
            SharedPreferences raw = context.getSharedPreferences(name, 0);
            Map<String, ?> all = raw.getAll();
            int total = all.size();
            int data = 0;
            for (String key : all.keySet()) {
                if (!key.startsWith("__androidx_security")) data++;
            }
            System.out.println("  (File has " + total + " raw entries, "
                    + data + " are data)");
        } catch (Exception e) {
            // ignore
        }
    }

    /**
     * Try COMMUNICATION_LIB_PREFERENCES via the AES-CBC SecurePreferences.
     * This is the fallback path StoragePreferences uses when Tink fails.
     * Uses password-based key derivation (no Android Keystore needed).
     */
    private static void dumpAesCbcPreferences(Context context,
            ClassLoader loader) {
        try {
            Class<?> cls = loader.loadClass(COMM_SECURE_PREFS_CLASS);
            Constructor<?> ctor = cls.getConstructor(
                    Context.class, String.class, String.class);
            // password = library package name, file = COMMUNICATION_LIB_PREFERENCES
            Object instance = ctor.newInstance(
                    context, COMM_LIB_PACKAGE, "COMMUNICATION_LIB_PREFERENCES");

            // This class implements SharedPreferences
            SharedPreferences prefs = (SharedPreferences) instance;

            int found = 0;
            // Try without MAC prefix
            for (String suffix : CREDENTIAL_SUFFIXES) {
                String value = prefs.getString(suffix, null);
                if (value != null && !value.isEmpty()) {
                    System.out.println("  " + suffix + " = " + value);
                    found++;
                }
            }

            // Try with discovered MAC addresses
            for (String mac : discoveredMacs) {
                for (String suffix : CREDENTIAL_SUFFIXES) {
                    String key = mac + suffix;
                    String value = prefs.getString(key, null);
                    if (value != null && !value.isEmpty()) {
                        System.out.println("  " + suffix + " = " + value);
                        found++;
                    }
                }
            }

            Map<String, ?> all = prefs.getAll();
            if (found == 0 && !dumpAll) {
                System.out.println("  No credentials found ("
                        + all.size() + " entries in file)");
            }

            if (dumpAll) {
                for (Map.Entry<String, ?> entry : all.entrySet()) {
                    System.out.println("  [raw] " + entry.getKey()
                            + " = " + entry.getValue());
                }
                System.out.println("  " + all.size()
                        + " total entries in file");
            }

        } catch (ClassNotFoundException e) {
            System.out.println("  SecurePreferences class not found in APK");
        } catch (Exception e) {
            System.out.println("  [FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
        }
    }

    /**
     * Create a dynamic proxy for the StringProvider interface used by
     * SecurePreferences. The interface has a method that concatenates strings.
     */
    private static Object createStringProviderProxy(Class<?> iface) {
        return java.lang.reflect.Proxy.newProxyInstance(
                iface.getClassLoader(),
                new Class<?>[]{iface},
                (proxy, method, methodArgs) -> {
                    if (method.getReturnType() == String.class
                            && methodArgs != null) {
                        StringBuilder sb = new StringBuilder();
                        for (Object arg : methodArgs) {
                            if (arg instanceof String[]) {
                                for (String s : (String[]) arg) {
                                    sb.append(s);
                                }
                            }
                        }
                        return sb.toString();
                    }
                    return null;
                });
    }

    /**
     * Try known credential key patterns against SecurePreferences.getString().
     * Uses MAC addresses discovered from SQLite and COMMUNICATION_LIB_PREFERENCES.
     */
    private static void tryKnownKeys(Object securePrefs, Method getString) {
        int found = 0;

        // Try without MAC prefix first
        for (String suffix : CREDENTIAL_SUFFIXES) {
            if (tryGetString(securePrefs, getString, suffix)) found++;
        }

        // Try with discovered MAC addresses
        for (String mac : discoveredMacs) {
            for (String suffix : CREDENTIAL_SUFFIXES) {
                if (tryGetString(securePrefs, getString, mac + suffix)) found++;
            }
        }

        if (found == 0) {
            System.out.println("  No credentials found");
        }
    }

    private static boolean tryGetString(Object prefs, Method getString,
            String key) {
        try {
            Object value = getString.invoke(prefs, key, null);
            if (value != null) {
                // Strip MAC prefix from display key for readability
                String displayKey = key;
                for (String mac : discoveredMacs) {
                    if (key.startsWith(mac)) {
                        displayKey = key.substring(mac.length());
                        break;
                    }
                }
                System.out.println("  " + displayKey + " = " + value);
                return true;
            }
        } catch (Exception e) {
            Throwable cause = e.getCause() != null ? e.getCause() : e;
            System.out.println("  [WARN] " + key + ": "
                    + cause.getClass().getSimpleName()
                    + ": " + cause.getMessage());
        }
        return false;
    }

    private static Method findGetStringMethod(Class<?> cls) {
        try {
            return cls.getMethod("getString", String.class, String.class);
        } catch (NoSuchMethodException e) {
            for (Method m : cls.getDeclaredMethods()) {
                Class<?>[] params = m.getParameterTypes();
                if (m.getReturnType() == String.class
                        && params.length == 2
                        && params[0] == String.class
                        && params[1] == String.class) {
                    m.setAccessible(true);
                    return m;
                }
            }
            return null;
        }
    }

    /**
     * Find a SharedPreferences field in the class or its superclasses.
     */
    private static SharedPreferences findPrefsField(Class<?> cls,
            Object instance) {
        Class<?> current = cls;
        while (current != null && current != Object.class) {
            for (Field f : current.getDeclaredFields()) {
                try {
                    f.setAccessible(true);
                    Object val = f.get(instance);
                    if (val instanceof SharedPreferences) {
                        return (SharedPreferences) val;
                    }
                } catch (Exception e) {
                    // Skip inaccessible fields
                }
            }
            current = current.getSuperclass();
        }
        return null;
    }
}
