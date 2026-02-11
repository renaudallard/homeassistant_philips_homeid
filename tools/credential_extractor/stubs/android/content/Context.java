package android.content;

/**
 * Minimal stub for compilation only.
 * At runtime, the real Android Context class is used.
 */
public abstract class Context {
    public static final int CONTEXT_INCLUDE_CODE = 1;
    public static final int CONTEXT_IGNORE_SECURITY = 2;
    public static final int MODE_PRIVATE = 0;

    public abstract Context createPackageContext(String packageName, int flags)
            throws Exception;

    public abstract SharedPreferences getSharedPreferences(String name, int mode);

    public abstract Object getSystemService(String name);

    public abstract ClassLoader getClassLoader();

    public abstract Context getApplicationContext();
}
