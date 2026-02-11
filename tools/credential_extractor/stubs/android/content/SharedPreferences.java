package android.content;

import java.util.Map;
import java.util.Set;

/**
 * Minimal stub for compilation only.
 * At runtime, the real Android SharedPreferences interface is used.
 */
public interface SharedPreferences {
    Map<String, ?> getAll();

    String getString(String key, String defValue);

    Set<String> getStringSet(String key, Set<String> defValues);

    int getInt(String key, int defValue);

    long getLong(String key, long defValue);

    float getFloat(String key, float defValue);

    boolean getBoolean(String key, boolean defValue);

    boolean contains(String key);
}
