import duckdb
import glob
from openai import OpenAI

# 1. 挂载本地 Ollama 引擎 (伪装成 OpenAI 客户端)
# 只要 Ollama 在后台运行，它默认监听 11434 端口
local_llm_client = OpenAI(
    base_url='http://localhost:11434/v1',
    api_key='ollama', # 本地调用不需要真实 Key
)

# 2. 定义大模型分类核心函数 (已扩充 MATCHES 分类)
def classify_csv_with_qwen(headers):
    # 构建严苛的系统指令，逼迫 7B 模型只输出标准路由词
    system_prompt = """
    你是一个无情的足球数据分类器。你只能根据我提供的 CSV 表头字段，输出以下 6 个词中的一个，绝不能包含任何标点符号、解释或多余的字：
    - PLAYERS：当包含年龄(Age)、位置(Position)、身价(Value)等球员特征时。
    - ODDS：当包含博彩机构(Company)、胜平负赔率(Win/Draw/Lose)、盘口时。
    - STATS：当包含积分(Points)、排名(Rank)、胜率(Win Rate)等球队战绩时。
    - INJURIES：当包含伤停原因(Reason/Injury)、缺阵(Absence)等信息时。
    - MATCHES：当包含主队(Home/HomeTeam)、客队(Away/AwayTeam)、比分(Score/Goals)、比赛时间(Date/Time)等赛事基础信息时。
    - UNKNOWN：当你完全无法判断时。
    """
    
    try:
        response = local_llm_client.chat.completions.create(
            model="qwen2.5:7b", # 确保你本地已经 pull 了这个模型
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请分类这段表头：{headers}"}
            ],
            temperature=0.1, # 温度调到最低，保证输出绝对稳定不发散
            max_tokens=10    # 限制输出长度，阻止它乱说话，提升推理速度
        )
        return response.choices[0].message.content.strip().upper()
    except Exception as e:
        print(f"⚠️ 本地模型调用失败: {e}")
        return "UNKNOWN"

# 3. 执行智能流水线
def run_smart_etl():
    conn = duckdb.connect('football_system.db')
    all_csv_files = glob.glob('*.csv')
    
    print(f"🚀 启动基于本地 Qwen2.5 的智能数据分拣流水线 (v2 终极版)，共 {len(all_csv_files)} 个文件...\n")

    for file_path in all_csv_files:
        try:
            # 第一步：DuckDB 极速嗅探表头
            # 【核心修复】加入 ignore_errors=true 强制跳过乱码字符，防止读取报错
            headers_df = conn.execute(f"SELECT * FROM read_csv_auto('{file_path}', ignore_errors=true) LIMIT 1").df()
            header_str = ", ".join(headers_df.columns.tolist())
            
            # 第二步：投喂给本地 7B 模型进行分类
            category = classify_csv_with_qwen(header_str)
            print(f"🤖 Qwen2.5 判定 [{file_path}] 为: {category}")

            # 第三步：根据模型输出结果，执行不同的清洗清洗逻辑
            if "PLAYERS" in category:
                print(f"   ➡️ 已送入【球员物理表】处理通道\n")
            elif "ODDS" in category:
                print(f"   ➡️ 已送入【赔率物理表】处理通道\n")
            elif "STATS" in category:
                print(f"   ➡️ 已送入【战绩物理表】处理通道\n")
            elif "INJURIES" in category:
                print(f"   ➡️ 已送入【伤停物理表】处理通道\n")
            elif "MATCHES" in category:
                print(f"   ➡️ 已送入【赛事赛程物理表】处理通道\n")
            else:
                print(f"   ⚠️ 无法分类，已将其隔离到【需人工确认】文件夹\n")

        except Exception as e:
            print(f"❌ 处理 {file_path} 时出错: {e}")

    conn.close()
    print("🎉 智能 ETL 落盘全部完成！")

# 启动！
if __name__ == "__main__":
    run_smart_etl()