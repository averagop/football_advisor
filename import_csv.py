import duckdb

# 1. 连上我们刚才建好的数据库
conn = duckdb.connect('football_system.db')

# 【注意】把你手头最乱的一个 CSV 文件放到 D:\Knowledge_Base 文件夹下
# 然后把下面这行代码里的 '你的文件名.csv' 替换成真实的名称
csv_file_path = 'Fifa.csv'

try:
    # 2. 核心魔法：使用 read_csv_auto 自动推断表头和格式，并直接存入名为 raw_test 的表
    # 如果表已经存在就先删掉，方便我们反复测试
    conn.execute("DROP TABLE IF EXISTS raw_test")
    conn.execute(f"CREATE TABLE raw_test AS SELECT * FROM read_csv_auto('{csv_file_path}')")
    
    # 3. 让 DuckDB 告诉我们，它解析出了哪些字段
    print("✅ CSV 文件导入成功！DuckDB 识别出的表头和数据类型如下：")
    columns = conn.execute("DESCRIBE raw_test").fetchall()
    for col in columns:
        print(f"  - 字段名: {col[0]} | 类型: {col[1]}")

    # 4. 预览前 3 行数据（使用 .df() 转成 Pandas 的 DataFrame，打印出来排版更好看）
    print("\n📊 前 3 行数据预览：")
    print(conn.execute("SELECT * FROM raw_test LIMIT 3").df())

except Exception as e:
    print(f"❌ 导入出错啦：{e}")

finally:
    # 养成好习惯，关闭连接
    conn.close()