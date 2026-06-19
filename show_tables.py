import duckdb

conn = duckdb.connect('football_system.db')

print("📂 当前数据库中的所有表：")
try:
    print(conn.execute("SHOW TABLES").df())
except Exception as e:
    print("查看表失败：", e)

print("\n📊 抽取核心表的前 5 行数据预览：")
try:
    print(conn.execute("SELECT * FROM core.dim_team_mapping LIMIT 5").df())
except Exception as e:
    print("查询预览失败：", e)

conn.close()