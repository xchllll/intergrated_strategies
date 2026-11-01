import sys
import os
import argparse
import csv
import glob
from pathlib import Path
import numpy as np
import joblib

# 必须要在导入 matplotlib.pyplot 之前设置非交互后端，防止在无 GUI 的板子上崩溃
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# =========================================================
# 修复：字体配置，解决混淆矩阵中文变方块的问题
# =========================================================
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# =========================================================
# 修复：绝对路径锚定
# =========================================================
CURRENT_FILE = Path(__file__).resolve()
ML_DIR = CURRENT_FILE.parent
DIAGNOSIS_DIR = ML_DIR.parent
ROOT_DIR = DIAGNOSIS_DIR.parent

sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(DIAGNOSIS_DIR))
sys.path.insert(0, str(ML_DIR))

from common.features import extract_features, to_vector, FEATURE_ORDER
from ml.gen_mock import build_sweeps, CLASSES
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix, ConfusionMatrixDisplay
from sklearn.pipeline import make_pipeline

def load_real_csv(filepath):
    """读取真实采集的 CSV 文件，提取特征"""
    freqs, mags, phases = [], [], []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            freqs.append(float(row['freq']))
            mags.append(float(row['mag']))
            phases.append(float(row['phase']))
    
    if not freqs:
        raise ValueError("CSV 文件为空")
        
    feats = extract_features(freqs, mags, phases)
    return to_vector(feats).reshape(1, -1)


def load_real_dataset(root):
    """读取 data/real/*.csv 下的真实采集数据，返回 (X, y, stats, skipped)。

    支持两种文件格式：
      * 长表（tools/collect_dut.py）：含 curve_id 列，一条曲线占连续多行
      * 宽表（data/gen_dataset.py）：freq,mag,phase,label，一个文件一条曲线

    幅值 <= -90 dB 的占位点会被丢弃；有效点少于 40 个的曲线整条跳过，
    避免残缺曲线污染训练集。
    """
    X, y = [], []
    stats = {}
    skipped = 0

    for path in sorted(glob.glob(os.path.join(root, "*.csv"))):
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue

        if "curve_id" in rows[0]:
            groups = {}
            for row in rows:
                groups.setdefault(row.get("curve_id") or "unknown", []).append(row)
            curves = list(groups.items())
        else:
            curves = [(os.path.splitext(os.path.basename(path))[0], rows)]

        for _cid, curve_rows in curves:
            freqs, mags, phases = [], [], []
            for row in curve_rows:
                try:
                    mag = float(row["mag"])
                    freq = float(row["freq"])
                    phase = float(row["phase"])
                except (KeyError, TypeError, ValueError):
                    continue
                if mag <= -90.0:          # -99 dB 占位点 / 无效点
                    continue
                freqs.append(freq)
                mags.append(mag)
                phases.append(phase)

            if len(freqs) < 40:
                skipped += 1
                continue

            label = curve_rows[0].get("label") or os.path.splitext(os.path.basename(path))[0]
            feats = extract_features(freqs, mags, phases)
            X.append(to_vector(feats))
            y.append(label)
            stats[label] = stats.get(label, 0) + 1

    return np.array(X), np.array(y), stats, skipped


def main():
    parser = argparse.ArgumentParser(description="电路拓扑识别模型训练")
    default_csv = ROOT_DIR / "data" / "dataset_rc_lpf.csv"
    
    parser.add_argument("--real-csv", type=str, default=str(default_csv), 
                        help="指定测试数据的 CSV 路径")
    # 【P0-1 修复】增加显式声明开关，不加这个开关，坚决不认它是“实测”
    parser.add_argument("--real", action="store_true", 
                        help="显式声明此 CSV 为真实硬件实测数据。如果不加，一律按 dry-run 仿真处理。")
    parser.add_argument("--real-dir", type=str, default=None,
                        help="用该目录下的真实采集数据训练（每条曲线一个样本）；"
                             "不给则该参数时仍用合成数据训练")
    args = parser.parse_args()

    if args.real_dir:
        print("1. 正在读取真实采集数据：%s" % args.real_dir)
        X, y, stats, skipped = load_real_dataset(args.real_dir)
        if len(X) == 0:
            raise SystemExit("真实数据为空：%s（先跑 tools/collect_dut.py）" % args.real_dir)
        print("   共 %d 条曲线（跳过 %d 条有效点不足的），%d 个特征"
              % (len(X), skipped, X.shape[1]))
        for label in sorted(stats):
            print("     %-16s %d 条" % (label, stats[label]))
        if min(stats.values()) < 2:
            raise SystemExit("有类别样本数 < 2，无法做分层划分；请每类至少采 2 条")
        train_source = "real"
    else:
        print("1. 正在生成合成数据并提取特征...")
        X, y = [], []
        for sweep_dict, label in build_sweeps(n_per_class=50):
            feats = extract_features(sweep_dict["freqs"], sweep_dict["mag_db"], sweep_dict["phase_deg"])
            X.append(to_vector(feats))
            y.append(label)

        X = np.array(X)
        y = np.array(y)
        print(f"   训练集生成完毕！共 {X.shape[0]} 个合成样本，{X.shape[1]} 个特征。")
        stats = {}
        train_source = "synthetic"

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    model_dir = ML_DIR / "model"
    report_dir = ROOT_DIR / "report"
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)

    # ----------------- 训练决策树 -----------------
    print("\n2. 正在训练 决策树 (Decision Tree)...")
    dt_pipeline = make_pipeline(StandardScaler(), DecisionTreeClassifier(max_depth=6, random_state=42))
    dt_pipeline.fit(X_train, y_train)
    dt_acc = accuracy_score(y_test, dt_pipeline.predict(X_test))
    print(f"   决策树测试集准确率: {dt_acc * 100:.2f}%")
    joblib.dump(dt_pipeline, model_dir / "topo_dt.joblib")

    # ----------------- 训练轻量神经网络 -----------------
    print("\n3. 正在训练 轻量神经网络 (MLP)...")
    mlp_pipeline = make_pipeline(
        StandardScaler(), 
        MLPClassifier(hidden_layer_sizes=(32, 16), max_iter=2000, random_state=42)
    )
    mlp_pipeline.fit(X_train, y_train)
    mlp_preds = mlp_pipeline.predict(X_test)
    mlp_acc = accuracy_score(y_test, mlp_preds)
    
    print(f"   神经网络测试集准确率: {mlp_acc * 100:.2f}%")
    _labels = sorted(set(y_test) | set(mlp_preds))
    print(classification_report(y_test, mlp_preds, labels=_labels,
                                target_names=[CLASSES.get(c, c) for c in _labels]))
    joblib.dump(mlp_pipeline, model_dir / "topo_mlp.joblib")

    # ===== 导出与库版本无关的 JSON（PYNQ 板子用 numpy 推理，不需要 sklearn）=====
    from export_model import export_pipeline
    json_path = export_pipeline(
        mlp_pipeline,
        model_dir / "topo_mlp.json",
        meta={"feature_order": FEATURE_ORDER, "trained_on": "synthetic"},
    )
    print("   已导出 %s（不依赖 sklearn 版本，%.1f KB）"
          % (json_path.name, json_path.stat().st_size / 1024.0))

    # ----------------- 生成混淆矩阵 -----------------
    print("\n4. 正在生成混淆矩阵图...")
    cm = confusion_matrix(y_test, mlp_preds, labels=_labels)
    disp = ConfusionMatrixDisplay(confusion_matrix=cm,
                                  display_labels=[CLASSES.get(c, c) for c in _labels])
    fig, ax = plt.subplots(figsize=(8, 6))
    disp.plot(cmap=plt.cm.Blues, ax=ax)
    plt.title("MLP Confusion Matrix (%s)" % ("Real Data" if train_source == "real" else "Synthetic Data"))
    plt.savefig(report_dir / "confusion_matrix.png", bbox_inches='tight')
    plt.close()

    # ----------------- 域外测试 (P0-1 核心逻辑) -----------------
    print("\n5. 正在使用外部数据测试 (Out-of-Distribution)...")
    is_dry_run = True
    real_test_result = "未找到 CSV 文件。"

    if train_source == "real":
        # 训练集本身就是实测数据，再拿 --real-csv 做"域外测试"没有意义
        real_test_result = ("本次训练集为真实采集数据，未执行额外的域外测试；"
                            "模型泛化能力请看上面的测试集指标。")
    elif os.path.exists(args.real_csv):
        if not args.real:
            # 文件存在，但没加 --real 开关
            real_test_result = f"文件 `{Path(args.real_csv).name}` 存在，但**未提供 `--real` 开关**。系统默认判定为合成/仿真数据（dry-run），不宣称实测结果。"
            is_dry_run = True
        else:
            # 传了 --real 开关，正式进入实测检验
            try:
                X_real = load_real_csv(args.real_csv)
                real_proba = mlp_pipeline.predict_proba(X_real)[0]
                best_idx = np.argmax(real_proba)
                pred_label = mlp_pipeline.classes_[best_idx]
                confidence = real_proba[best_idx]
                
                real_test_result = (
                    f"真实数据 `{Path(args.real_csv).name}` 被模型判定为: **{CLASSES.get(pred_label, pred_label)}**，"
                    f"置信度: **{confidence * 100:.2f}%**。\n\n"
                    f"> 注：此数据**未参与训练**，仅用于检验合成模型在真实硬件上的泛化能力。"
                )
                is_dry_run = False
            except Exception as e:
                real_test_result = f"读取真实 CSV 失败: {e}"
    else:
        real_test_result = f"找不到文件 `{args.real_csv}`，跳过测试。"

    # ----------------- 生成 Markdown 报告 -----------------
    print("\n6. 正在生成训练报告...")
    report_path = report_dir / "ml_training_note.md"
    with open(report_path, "w", encoding="utf-8") as f:
        # 【P0-1 修复】只有显式 --real 通过后，标题才叫实测检验
        if train_source == "real":
            title_type = "实测数据训练"
        else:
            title_type = "实测检验" if not is_dry_run else "dry-run 仿真（合成自检）"
        f.write(f"# ML 训练报告 ({title_type})\n\n")
        
        f.write("## 1. 数据来源与训练策略\n")
        if train_source == "real":
            dist = "、".join("%s %d 条" % (k, v) for k, v in sorted(stats.items()))
            f.write("- **训练集**: 真实硬件采集，共 %d 条曲线（%s）。\n" % (len(X), dist))
            f.write("- **划分**: 每条曲线一个样本，按类别分层抽取 20% 作为测试集。\n")
            f.write("- **数据来源**: `tools/collect_dut.py` 通过网页后端采集，"
                    "已应用校准表与输入网络去嵌入。\n\n")
        else:
            f.write("- **训练集**: 100% 纯合成数据 (通过 `gen_mock.py` 物理公式生成)。\n")
            f.write("- **测试集**: 从合成数据中划分的 20% 数据。\n")
            f.write("- **域外测试 (OOD)**: 必须显式传入 `--real` 且文件存在，"
                    "才认定为真实硬件采集的 CSV，防止用仿真数据伪造实测。\n\n")
        
        f.write("## 2. 模型性能 (%s)\n" % ("实测数据" if train_source == "real" else "基于合成数据"))
        f.write(f"- **决策树 (DT) 准确率**: {dt_acc * 100:.2f}%\n")
        f.write(f"- **神经网络 (MLP) 准确率**: {mlp_acc * 100:.2f}%\n")
        
        if train_source != "real":
            f.write("> *注：由于训练集和测试集均来自高度理想的合成数据，模型极易达到极高准确率。该数字仅反映算法在合成数据上的可分性，不代表真实硬件环境下的泛化表现。真实表现请参考下文的域外测试结果。*\n\n")
        
        f.write("### 混淆矩阵\n")
        f.write("![Confusion Matrix](./confusion_matrix.png)\n\n")
        
        f.write("## 3. 外部数据测试结果\n")
        f.write(real_test_result + "\n")
        
    print(f"\n 训练与评估完成！报告已保存至 {report_path}")
    if train_source == "synthetic" and is_dry_run:
        print(" 当前为 dry-run 仿真测试，若要声明为真实实测，请添加 --real 参数。")

if __name__ == "__main__":
    main()