"""最终完整性验证"""
import sys
sys.path.insert(0, ".")
import warnings
from football_advisor.multi_source_coordinator import MultiSourceCoordinator
from football_advisor.config import SyncConfig

coordinator = MultiSourceCoordinator(SyncConfig(), duckdb_path="data/football_advisor.duckdb")

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    sr = coordinator.fetch_all("匈牙利", "哈萨克")

    coro_warnings = [
        x for x in w
        if "coroutine" in str(x.message).lower()
        or "never awaited" in str(x.message).lower()
    ]
    if coro_warnings:
        for cw in coro_warnings:
            print(f"WARN: {cw.message}")
    else:
        print("OK: 零 coroutine 警告")

    print(f"总源数: {len(sr.results)}")
    for r in sr.results:
        icon = "OK" if r.status == "success" else "FAIL"
        print(f"  [{icon}] {r.provider_name}")

    st = next(
        (r for r in sr.results if r.provider_name == "SportteryOfficialWeb"),
        None,
    )
    if st and st.data:
        unsold = st.data.get("lottery_unsold_playtypes", [])
        if unsold:
            print(f"竞彩未开售玩法: {unsold}")
        else:
            print("竞彩5种玩法全部开售: YES")