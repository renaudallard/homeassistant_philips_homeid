package android.database.sqlite;

import android.database.Cursor;

/**
 * Minimal stub for compilation only.
 * At runtime, the real Android SQLiteDatabase class is used.
 */
public class SQLiteDatabase {
    public interface CursorFactory {}

    public static final int OPEN_READONLY = 1;

    public static SQLiteDatabase openDatabase(
            String path, CursorFactory factory, int flags) {
        return null;
    }

    public Cursor rawQuery(String sql, String[] selectionArgs) {
        return null;
    }

    public void close() {}
}
