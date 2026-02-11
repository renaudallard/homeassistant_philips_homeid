import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * Extracts Philips HomeID app credentials from the app's local storage.
 *
 * Tries multiple extraction methods in order:
 * 1. SQLite database (network_node.db) - used by older firmwares
 * 2. EncryptedSharedPreferences (COMMUNICATION_LIB_PREFERENCES) - newer firmwares
 * 3. Core SecurePreferences (ONE_KA_ENCRYPTED_PREFERENCES) - with XOR layer
 * 4. Plain SharedPreferences - fallback
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

    public static void main(String[] args) {
        // Early output to confirm main() was reached
        System.out.println("Philips HomeID Credential Extractor");
        System.out.println("====================================");
        System.out.println();
        System.out.flush();

        try {
            Context context = getAppContext();
            System.out.println("[OK] Got context for " + PKG);
            System.out.flush();

            // Register AndroidKeyStore provider — required for
            // EncryptedSharedPreferences (Tink) decryption in app_process
            registerAndroidKeyStore();

            // Get the classloader that includes the Philips APK's classes
            ClassLoader apkLoader = context.getClassLoader();
            System.out.println("[OK] Got APK classloader: "
                    + apkLoader.getClass().getName());
            System.out.println();

            // Method 1: SQLite database (older firmwares)
            System.out.println("--- SQLite Database (network_node.db) ---");
            dumpSqliteDatabase();

            // Method 2: WiFi credentials via StoragePreferences
            System.out.println();
            System.out.println("--- WiFi Credentials (COMMUNICATION_LIB_PREFERENCES) ---");
            dumpStoragePreferences(context, apkLoader);

            // Method 3: App preferences via core SecurePreferences
            System.out.println();
            System.out.println("--- App Preferences (ONE_KA_ENCRYPTED_PREFERENCES) ---");
            dumpCoreSecurePreferences(context, apkLoader);

            // Method 4: raw SharedPreferences (unencrypted view)
            System.out.println();
            System.out.println("--- Plain SharedPreferences (fallback) ---");
            dumpPlainPrefs(context, "ONE_KA_PREFERENCES_SECURE");
            dumpPlainPrefs(context, "network_node");

        } catch (Throwable t) {
            System.err.println("[FAIL] " + t.getClass().getName() + ": " + t.getMessage());
            t.printStackTrace(System.err);
            System.err.println();
            System.err.println("Troubleshooting:");
            System.err.println("  - Are you running as the Philips app's UID?");
            System.err.println("  - Is the Philips HomeID app installed and paired?");
        }
    }

    /**
     * Get an Android Context for the Philips app package.
     *
     * Creates an ActivityThread without calling attach() to avoid system-level
     * initialization that requires system UID permissions. Then uses
     * createPackageContext with CONTEXT_INCLUDE_CODE to load the Philips APK's
     * classes into the context's classloader.
     */
    private static Context getAppContext() throws Exception {
        // Prepare the main looper (required by many Android APIs)
        try {
            Class.forName("android.os.Looper")
                    .getMethod("prepareMainLooper").invoke(null);
        } catch (Exception e) {
            System.out.println("[DEBUG] Looper: " + e.getMessage());
        }

        Class<?> atClass = Class.forName("android.app.ActivityThread");

        // Method 1: Create ActivityThread without attach()
        // attach(true) requires system UID, attach(false) does IPC to ActivityManager.
        // Neither works from app_process as a regular app UID.
        // But getSystemContext() works without attach().
        try {
            System.out.println("[DEBUG] Trying ActivityThread without attach...");
            Constructor<?> ctor = atClass.getDeclaredConstructor();
            ctor.setAccessible(true);
            Object thread = ctor.newInstance();

            // Set as current ActivityThread (normally done by attach())
            // Required for ActivityThread.currentApplication() used by KeyStore
            try {
                Field sThreadField = atClass.getDeclaredField("sCurrentActivityThread");
                sThreadField.setAccessible(true);
                sThreadField.set(null, thread);
            } catch (Exception e) {
                System.out.println("[DEBUG] sCurrentActivityThread: " + e.getMessage());
            }

            Context sysContext =
                    (Context) atClass.getMethod("getSystemContext").invoke(thread);
            Context pkgContext = sysContext.createPackageContext(
                    PKG, Context.CONTEXT_INCLUDE_CODE | Context.CONTEXT_IGNORE_SECURITY);

            // Set up an Application so that ActivityThread.currentApplication()
            // returns non-null. android.security.KeyStore calls this to get a
            // Context and throws IllegalStateException if it returns null.
            setupApplication(atClass, thread, pkgContext);

            return pkgContext;
        } catch (Exception e) {
            System.out.println("[DEBUG] Method 1 failed: "
                    + e.getClass().getSimpleName() + ": " + e.getMessage());
        }

        // Method 2: systemMain() — fallback, may crash on some devices
        try {
            System.out.println("[DEBUG] Trying ActivityThread.systemMain...");
            Object thread = atClass.getMethod("systemMain").invoke(null);
            Context sysContext =
                    (Context) atClass.getMethod("getSystemContext").invoke(thread);
            Context pkgContext = sysContext.createPackageContext(
                    PKG, Context.CONTEXT_INCLUDE_CODE | Context.CONTEXT_IGNORE_SECURITY);
            setupApplication(atClass, thread, pkgContext);
            return pkgContext;
        } catch (Exception e) {
            System.out.println("[DEBUG] Method 2 failed: "
                    + e.getClass().getSimpleName() + ": " + e.getMessage());
        }

        throw new Exception("Could not create Android Context (all methods failed)");
    }

    /**
     * Create an Application wrapping the given context and set it as the
     * ActivityThread's initial application. This makes
     * ActivityThread.currentApplication() return non-null, which is required
     * by android.security.KeyStore.getApplicationContext() for Tink/Jetpack
     * EncryptedSharedPreferences to access the master key.
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

            System.out.println("[OK] Set up Application for KeyStore access");
        } catch (Exception e) {
            System.out.println("[DEBUG] Application setup: "
                    + e.getClass().getSimpleName() + ": " + e.getMessage());
        }
    }

    /**
     * Register the AndroidKeyStore security provider.
     *
     * In a normal Android app process the provider is pre-registered, but when
     * running via app_process it is missing. Without it, Tink/Jetpack
     * EncryptedSharedPreferences cannot access the master key and decryption
     * fails with "AndroidKeyStore not found".
     *
     * The provider class moved in Android 12 (API 31):
     *   API 31+: android.security.keystore2.AndroidKeyStoreProvider
     *   API 23-30: android.security.keystore.AndroidKeyStoreProvider
     * Both expose a static install() method.
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
                System.out.println("[OK] Registered AndroidKeyStore via " + className);
                return;
            } catch (ClassNotFoundException e) {
                // Try next class
            } catch (Exception e) {
                System.out.println("[DEBUG] " + className + ".install(): "
                        + e.getClass().getSimpleName() + ": " + e.getMessage());
            }
        }
        System.out.println("[WARN] Could not register AndroidKeyStore provider"
                + " — EncryptedSharedPreferences may not work");
    }

    /**
     * Read credentials from the SQLite database (network_node.db).
     * Older firmwares store client_id, client_secret, and encryption_key here.
     */
    private static void dumpSqliteDatabase() {
        String dataDir = "/data/data/" + PKG;
        String dbPath = dataDir + "/databases/network_node.db";

        File dbFile = new File(dbPath);
        if (!dbFile.exists()) {
            System.out.println("[SKIP] Database not found: " + dbPath);
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
                    }
                }
            }
            if (rowNum == 0) {
                System.out.println("[SKIP] Database is empty");
            } else {
                System.out.println("[OK] " + rowNum + " rows found");
            }

        } catch (Exception e) {
            System.out.println("[FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
            e.printStackTrace(System.out);
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
     * Open COMMUNICATION_LIB_PREFERENCES via the Philips app's StoragePreferences
     * class. This class internally handles EncryptedSharedPreferences (Tink) setup,
     * using the obfuscated Jetpack classes bundled in the APK.
     *
     * Classes are loaded from the APK via the context's classloader (set up by
     * createPackageContext with CONTEXT_INCLUDE_CODE).
     */
    private static void dumpStoragePreferences(Context context, ClassLoader loader) {
        try {
            Class<?> cls = loader.loadClass(STORAGE_PREFS_CLASS);
            Object instance = cls.getConstructor(Context.class).newInstance(context);

            // Find the underlying SharedPreferences field by type
            SharedPreferences prefs = findPrefsField(cls, instance);
            if (prefs == null) {
                System.out.println("[FAIL] No SharedPreferences field found");
                return;
            }

            Map<String, ?> all = prefs.getAll();
            int count = 0;
            for (Map.Entry<String, ?> entry : all.entrySet()) {
                String key = entry.getKey();
                // Skip Tink internal keyset entries
                if (key.startsWith("__androidx_security")) continue;
                System.out.println("  " + key + " = " + entry.getValue());
                count++;
            }
            System.out.println("[OK] " + count + " entries found");

        } catch (ClassNotFoundException e) {
            System.out.println("[FAIL] StoragePreferences class not found");
            System.out.println("  Class: " + STORAGE_PREFS_CLASS);
            System.out.println("  Loader: " + loader.getClass().getName());
        } catch (Exception e) {
            System.out.println("[FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
            e.printStackTrace(System.out);
        }
    }

    /**
     * Open ONE_KA_ENCRYPTED_PREFERENCES via the Philips app's core SecurePreferences.
     * This class wraps EncryptedSharedPreferences with an additional XOR layer:
     * - Keys are split into 4 SHA-1 hashed parts
     * - Values are hex-encoded XOR of plaintext with package name, split into 4 chunks
     *
     * We try to use the getString method directly with known credential key patterns.
     */
    private static void dumpCoreSecurePreferences(Context context, ClassLoader loader) {
        try {
            Class<?> cls = loader.loadClass(SECURE_PREFS_CLASS);

            // Find a constructor we can use
            Object instance = null;
            for (Constructor<?> ctor : cls.getDeclaredConstructors()) {
                Class<?>[] params = ctor.getParameterTypes();
                // Looking for (Context, String, <StringProvider>)
                if (params.length == 3
                        && params[0].isAssignableFrom(context.getClass())
                        && params[1] == String.class) {
                    ctor.setAccessible(true);
                    // Create a proxy for StringProvider (concatenation helper)
                    Object stringProvider = createStringProviderProxy(params[2]);
                    instance = ctor.newInstance(context, PKG, stringProvider);
                    break;
                }
            }

            if (instance == null) {
                System.out.println("[FAIL] Could not find suitable constructor");
                return;
            }

            // Try getString with known credential key patterns
            Method getStringMethod = findGetStringMethod(cls);
            if (getStringMethod != null) {
                System.out.println("  Searching for known credential keys...");
                tryKnownKeys(instance, getStringMethod);
            }

            // Also dump the raw underlying SharedPreferences
            SharedPreferences prefs = findPrefsField(cls, instance);
            if (prefs != null) {
                Map<String, ?> all = prefs.getAll();
                int count = 0;
                for (Map.Entry<String, ?> entry : all.entrySet()) {
                    String key = entry.getKey();
                    if (key.startsWith("__androidx_security")) continue;
                    Object value = entry.getValue();
                    System.out.println("  [raw] " + key + " = " + value);
                    // Try XOR decryption on hex-looking values
                    if (value instanceof String) {
                        String decrypted = tryXorDecrypt((String) value);
                        if (decrypted != null) {
                            System.out.println("  [xor] -> " + decrypted);
                        }
                    }
                    count++;
                }
                System.out.println("[OK] " + count + " raw entries");
            }

        } catch (ClassNotFoundException e) {
            System.out.println("[SKIP] SecurePreferences class not found in APK");
        } catch (Exception e) {
            System.out.println("[FAIL] " + e.getClass().getSimpleName()
                    + ": " + e.getMessage());
            e.printStackTrace(System.out);
        }
    }

    /**
     * Create a dynamic proxy for the StringProvider interface used by
     * SecurePreferences. The interface has a method c(String, String[])
     * that concatenates string array elements.
     */
    private static Object createStringProviderProxy(Class<?> iface) {
        return java.lang.reflect.Proxy.newProxyInstance(
                iface.getClassLoader(),
                new Class<?>[]{iface},
                (proxy, method, methodArgs) -> {
                    // StringProvider.c(separator, parts) -> concatenate
                    if (method.getReturnType() == String.class && methodArgs != null) {
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
     * Keys follow the pattern: {macAddress}DEVICE_CLIENT_ID etc.
     * SecurePreferences handles the SHA-1 key splitting and XOR decryption internally.
     */
    private static void tryKnownKeys(Object securePrefs, Method getString) {
        String[] suffixes = {
            "DEVICE_CLIENT_ID", "DEVICE_CLIENT_SECRET",
            "DEVICE_HSDP_ID", "DEVICE_CPP_ID"
        };

        // Try with empty prefix (some versions don't use MAC prefix)
        for (String suffix : suffixes) {
            tryGetString(securePrefs, getString, suffix);
        }

        // Try with example MAC pattern (Philips OUI prefix)
        // MAC addresses are stored with colons, lowercase
        String[] knownMacs = {"e4:bc:96:00:00:00"};
        for (String mac : knownMacs) {
            for (String suffix : suffixes) {
                tryGetString(securePrefs, getString, mac + suffix);
            }
        }
    }

    private static void tryGetString(Object prefs, Method getString, String key) {
        try {
            Object value = getString.invoke(prefs, key, null);
            if (value != null) {
                System.out.println("  [found] " + key + " = " + value);
            }
        } catch (Exception e) {
            // Key not found or decryption failed
        }
    }

    private static Method findGetStringMethod(Class<?> cls) {
        try {
            return cls.getMethod("getString", String.class, String.class);
        } catch (NoSuchMethodException e) {
            // Try declared methods
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
    private static SharedPreferences findPrefsField(Class<?> cls, Object instance) {
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

    /**
     * Try to XOR-decrypt a hex-encoded string using the package name as key.
     * Returns null if the value doesn't look like hex or decryption produces garbage.
     */
    private static String tryXorDecrypt(String hexValue) {
        if (hexValue == null || hexValue.isEmpty()) return null;
        if (!hexValue.matches("[0-9a-fA-F]+")) return null;
        if (hexValue.length() < 2) return null;

        try {
            byte[] data = hexToBytes(hexValue);
            byte[] key = PKG.getBytes(StandardCharsets.UTF_8);
            byte[] result = new byte[data.length];
            for (int i = 0; i < data.length; i++) {
                result[i] = (byte) (data[i] ^ key[i % key.length]);
            }
            // Check if result is printable ASCII/UTF-8
            String decoded = new String(result, StandardCharsets.UTF_8);
            if (isPrintable(decoded)) {
                return decoded;
            }
        } catch (Exception e) {
            // Not valid hex
        }
        return null;
    }

    private static byte[] hexToBytes(String hex) {
        if (hex.length() % 2 != 0) hex = "0" + hex;
        int len = hex.length() / 2;
        byte[] result = new byte[len];
        for (int i = 0; i < len; i++) {
            result[i] = (byte) ((Character.digit(hex.charAt(i * 2), 16) << 4)
                    + Character.digit(hex.charAt(i * 2 + 1), 16));
        }
        return result;
    }

    private static boolean isPrintable(String s) {
        for (char c : s.toCharArray()) {
            if (c < 0x20 || c == 0x7f) return false;
        }
        return s.length() > 0;
    }

    /**
     * Dump plain (unencrypted) SharedPreferences as a fallback.
     * For EncryptedSharedPreferences files this shows raw encrypted entries.
     */
    private static void dumpPlainPrefs(Context context, String name) {
        try {
            SharedPreferences prefs = context.getSharedPreferences(name, 0);
            Map<String, ?> all = prefs.getAll();
            if (all == null || all.isEmpty()) return;
            System.out.println("  " + name + " (" + all.size() + " entries):");
            for (Map.Entry<String, ?> entry : all.entrySet()) {
                System.out.println("    " + entry.getKey() + " = " + entry.getValue());
            }
        } catch (Exception e) {
            System.out.println("  [FAIL] " + name + ": " + e.getMessage());
        }
    }
}
