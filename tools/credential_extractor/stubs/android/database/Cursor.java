package android.database;

/**
 * Minimal stub for compilation only.
 * At runtime, the real Android Cursor interface is used.
 */
public interface Cursor {
    boolean moveToNext();

    String getString(int columnIndex);

    int getColumnIndex(String columnName);

    int getColumnCount();

    String getColumnName(int columnIndex);

    boolean isNull(int columnIndex);

    void close();
}
