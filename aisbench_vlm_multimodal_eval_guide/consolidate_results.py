#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AISBench 多模态精度测试结果整合脚本（方案1：两次独立运行后合并）

把两次运行的结果合并到一个 Excel：
  - infer 运行 (--mode infer)：predictions/<模型abbr>/mm_custom.jsonl  -> 模型预测文本
  - perf  运行 (--mode perf) ：performances/<模型abbr>/mm_custom_details.jsonl + 同目录 .db -> 每请求时延
  - 原始数据集 (/root/mydata/mm.jsonl)                                   -> 图片路径/问题/答案

对齐键：每条记录里的 `id` 字段（= 数据集行号，两次运行稳定一致）。

时延计算（从 perf 的 time_points 解码）：
  - E2E  = (tp[-1] - tp[0]) * 1000          总完成时间(ms)
  - TTFT = (tp[1]  - tp[0]) * 1000          首 token 延迟(ms)，需流式才有意义
  - TPOT = mean(diff(tp)[1:]) * 1000        token 间平均延迟(ms)，排除首段 TTFT
  注意：非流式(stream=False)只有2个时间点，TTFT≈E2E、TPOT=0；要真实 TTFT/TPOT 请用
        vllm_api_stream_chat(stream=True) 跑 perf。

用法：
  python consolidate_results.py \
      --infer  outputs/default/<时间戳1>/predictions/vllm-api-stream-chat/mm_custom.jsonl \
      --perf   outputs/default/<时间戳2>/performances/vllm-api-stream-chat/mm_custom_details.jsonl \
      --dataset /root/mydata/mm.jsonl \
      --out    result.xlsx

依赖：pip install pandas openpyxl numpy
"""

import argparse
import glob
import io
import json
import os
import sqlite3
import statistics
import sys
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import pandas as pd
except ImportError:
    sys.exit("缺少 pandas，请先：pip install pandas openpyxl numpy")


# ----------------------------- 基础工具 ----------------------------- #

def read_jsonl(path: str) -> List[dict]:
    """逐行读 jsonl，跳过空行。"""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARN] 跳过无法解析的行: {e}", file=sys.stderr)
    return records


def index_by_id(records: List[dict]) -> Dict[Any, dict]:
    """以 id 为键建索引。"""
    out = {}
    for r in records:
        key = r.get("id")
        if key is None:
            continue
        out[key] = r
    return out


# ----------------------------- time_points 解码 ----------------------------- #

class TimePointsDB:
    """从 perf 同目录的 .db 里解码 time_points(numpy 数组)。
    jsonl 里 time_points 形如 {"__db_ref__": <rowid>}，对应 sqlite 表
    numpy_store(id, arr_blob)，blob 是 np.save 序列化的数组。"""

    def __init__(self, perf_jsonl_path: str, hint_db_name: Optional[str] = None):
        self.conn = None
        db_path = self._locate_db(perf_jsonl_path, hint_db_name)
        if db_path:
            try:
                self.conn = sqlite3.connect(db_path)
            except sqlite3.Error as e:
                print(f"[WARN] 打开 db 失败 {db_path}: {e}", file=sys.stderr)

    @staticmethod
    def _locate_db(perf_jsonl_path: str, hint_db_name: Optional[str]) -> Optional[str]:
        d = os.path.dirname(os.path.abspath(perf_jsonl_path))
        stem = os.path.splitext(os.path.basename(perf_jsonl_path))[0]  # mm_custom_details
        # 1) 同名 .db
        cand = os.path.join(d, stem + ".db")
        if os.path.exists(cand):
            return cand
        # 2) 记录里登记的 db_name
        if hint_db_name:
            cand = os.path.join(d, hint_db_name)
            if os.path.exists(cand):
                return cand
        # 3) 目录里任意 .db
        dbs = sorted(glob.glob(os.path.join(d, "*.db")))
        if dbs:
            print(f"[INFO] 未找到同名 db，使用: {dbs[0]}", file=sys.stderr)
            return dbs[0]
        return None

    def resolve(self, tp_field: Any) -> Optional[np.ndarray]:
        """把 time_points 字段解析成 numpy 数组。兼容内联 list 与 __db_ref__。"""
        if tp_field is None:
            return None
        # 内联（旧版本/已展平）
        if isinstance(tp_field, (list, tuple)):
            return np.asarray(tp_field, dtype=np.float64)
        if isinstance(tp_field, dict) and "__db_ref__" in tp_field:
            ref = tp_field["__db_ref__"]
            try:
                ref = int(ref)
            except (TypeError, ValueError):
                return None
            if self.conn is None:
                return None
            try:
                row = self.conn.execute(
                    "SELECT arr_blob FROM numpy_store WHERE id=?", (ref,)
                ).fetchone()
                if not row:
                    return None
                return np.load(io.BytesIO(row[0]), allow_pickle=False)
            except Exception as e:
                print(f"[WARN] 解码 time_points(db_ref={ref}) 失败: {e}", file=sys.stderr)
                return None
        return None


def compute_latency_ms(tp: Optional[np.ndarray]) -> Dict[str, float]:
    """从时间戳数组算 TTFT/TPOT/E2E(ms)。失败返回 NaN。"""
    nan = float("nan")
    res = {"e2e_ms": nan, "ttft_ms": nan, "tpot_ms": nan,
           "itl_median_ms": nan, "num_chunks": 0}
    if tp is None or len(tp) < 2:
        if tp is not None:
            res["num_chunks"] = int(len(tp))
        return res
    tp = np.asarray(tp, dtype=np.float64)
    diffs = np.diff(tp)  # 相邻时间点间隔(秒)
    res["e2e_ms"] = round(float((tp[-1] - tp[0]) * 1000), 3)
    res["ttft_ms"] = round(float(diffs[0] * 1000), 3)             # 首 token
    if len(diffs) > 1:
        inter = diffs[1:] * 1000                                  # 排除首段(TTFT)
        res["tpot_ms"] = round(float(np.mean(inter)), 3)
        res["itl_median_ms"] = round(float(np.median(inter)), 3)
    else:
        res["tpot_ms"] = 0.0                                      # 仅2点时无 token 间隔
    res["num_chunks"] = int(len(tp))
    return res


def pct(values: List[float], q: float) -> float:
    """简易百分位，忽略 NaN。"""
    vals = [v for v in values if v == v]  # NaN != NaN
    if not vals:
        return float("nan")
    vals = sorted(vals)
    k = (len(vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


# ----------------------------- 主流程 ----------------------------- #

def build_rows(infer_map, perf_map, dataset_map, tdb: TimePointsDB) -> List[dict]:
    all_ids = sorted(set(infer_map) | set(perf_map) | set(dataset_map),
                     key=lambda x: (x is None, x))
    rows = []
    for i in all_ids:
        ds = dataset_map.get(i, {})
        inf = infer_map.get(i, {})
        pf = perf_map.get(i, {})

        # 预测：优先 infer，回退 perf
        prediction = inf.get("prediction", pf.get("prediction", ""))
        success = inf.get("success", pf.get("success", None))

        # 时延：仅 perf 有
        lat = compute_latency_ms(tdb.resolve(pf.get("time_points"))) if pf else \
            compute_latency_ms(None)

        # 图片路径（多图用 ; 连接）
        paths = ds.get("path") or []
        image = "; ".join(paths) if isinstance(paths, list) else str(paths)

        rows.append({
            "id": i,
            "image": image,
            "question": ds.get("question", ""),
            "answer": ds.get("answer", ""),
            "prediction": prediction,
            "success": success,
            "input_tokens": pf.get("input_tokens", ""),
            "output_tokens": pf.get("output_tokens", ""),
            "num_chunks": lat["num_chunks"],
            "ttft_ms": lat["ttft_ms"],
            "tpot_ms": lat["tpot_ms"],
            "itl_median_ms": lat["itl_median_ms"],
            "e2e_ms": lat["e2e_ms"],
        })
    return rows


def build_summary(rows: List[dict]) -> List[dict]:
    n = len(rows)
    succ = [r for r in rows if r["success"]]
    e2e = [r["e2e_ms"] for r in rows if isinstance(r["e2e_ms"], (int, float))]
    ttft = [r["ttft_ms"] for r in rows if isinstance(r["ttft_ms"], (int, float))]
    tpot = [r["tpot_ms"] for r in rows if isinstance(r["tpot_ms"], (int, float))]
    in_tok = [r["input_tokens"] for r in rows if isinstance(r["input_tokens"], (int, float))]
    out_tok = [r["output_tokens"] for r in rows if isinstance(r["output_tokens"], (int, float))]

    def agg(name, vals, extra_p90=True):
        out = {"指标": name, "样本数": len(vals)}
        if vals:
            out.update({"平均": round(statistics.mean(vals), 3),
                        "中位数": round(statistics.median(vals), 3)})
            if extra_p90:
                out["P90"] = round(pct(vals, 0.9), 3)
        return out

    summary = [
        {"指标": "总请求数", "样本数": n, "平均": "", "中位数": "", "P90": ""},
        {"指标": "成功请求数", "样本数": len(succ), "平均": "", "中位数": "", "P90": ""},
        {"指标": "成功率", "样本数": n,
         "平均": f"{len(succ)/n*100:.2f}%" if n else "", "中位数": "", "P90": ""},
        agg("E2E(ms)", e2e),
        agg("TTFT(ms)", ttft),
        agg("TPOT(ms)", tpot),
        agg("ITL中位数(ms)", [r["itl_median_ms"] for r in rows
                             if isinstance(r["itl_median_ms"], (int, float))]),
        agg("input_tokens", in_tok, extra_p90=False),
        agg("output_tokens", out_tok, extra_p90=False),
    ]
    # 粗略吞吐：总输出token / 总E2E秒
    if e2e and out_tok and sum(e2e) > 0:
        throughput = sum(out_tok) / (sum(e2e) / 1000.0)
        summary.append({"指标": "输出吞吐(估, tok/s)", "样本数": len(out_tok),
                        "平均": round(throughput, 2), "中位数": "", "P90": ""})
    return summary


def write_excel(rows: List[dict], summary: List[dict], out_path: str):
    df_det = pd.DataFrame(rows)
    df_sum = pd.DataFrame(summary)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_sum.to_excel(writer, sheet_name="汇总", index=False)
        df_det.to_excel(writer, sheet_name="明细", index=False)
        # 列宽自适应
        for ws_name, df in (("汇总", df_sum), ("明细", df_det)):
            ws = writer.sheets[ws_name]
            for idx, col in enumerate(df.columns, start=1):
                maxlen = max(
                    [len(str(col))] + [len(str(v)) for v in df[col].head(200)]
                )
                ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = min(maxlen + 4, 80)
            ws.freeze_panes = "A2"
    print(f"[OK] 已写出: {out_path}")


def main():
    ap = argparse.ArgumentParser(description="AISBench 精度+时延结果整合到 Excel")
    ap.add_argument("--infer", required=True,
                    help="infer 运行的 mm_custom.jsonl (predictions/<模型abbr>/ 下)")
    ap.add_argument("--perf", required=True,
                    help="perf 运行的 mm_custom_details.jsonl (performances/<模型abbr>/ 下)")
    ap.add_argument("--dataset", default=None,
                    help="原始数据集 mm.jsonl（可选，用于补图片路径/问题/答案列）")
    ap.add_argument("--out", default="result.xlsx", help="输出 Excel 路径")
    args = ap.parse_args()

    print(f"[1/4] 读取 infer 预测: {args.infer}")
    infer_recs = read_jsonl(args.infer)
    infer_map = index_by_id(infer_recs)

    print(f"[2/4] 读取 perf 明细: {args.perf}")
    perf_recs = read_jsonl(args.perf)
    perf_map = index_by_id(perf_recs)
    hint_db = perf_recs[0].get("db_name") if perf_recs else None
    tdb = TimePointsDB(args.perf, hint_db_name=hint_db)

    dataset_map = {}
    if args.dataset:
        print(f"[3/4] 读取原始数据集: {args.dataset}")
        ds_recs = read_jsonl(args.dataset)
        # 数据集本身没有 id 字段，按行号(从0)建索引，与 ais_bench 的 id 对齐
        dataset_map = {i: r for i, r in enumerate(ds_recs)}
    else:
        print("[3/4] 未提供 --dataset，跳过图片/问题列")

    print("[4/4] 合并与写 Excel ...")
    rows = build_rows(infer_map, perf_map, dataset_map, tdb)
    summary = build_summary(rows)
    write_excel(rows, summary, args.out)

    # 简要回显
    print("\n=== 汇总 ===")
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
