import sys
import os
import argparse
import csv
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

def main():
    parser = argparse.ArgumentParser(description="电路拓扑识别模型训练")
    default_csv = ROOT_DIR / "data" / "dataset_rc_lpf.csv"
    
    parser.add_argument("--real-csv", type=str, default=str(default_csv), 
                        help="指定测试数据的 CSV 路径")
    # 【P0-1 修复】增加显式声明开关，不加这个开关，坚决不认它是“实测”
    parser.add_argument("--real", action="store_true", 
                        help="显式声明此 CSV 为真实硬件实测数据。如果不加，一律按 dry-run 仿真处理。")
    args = parser.parse_args()

    print("1. 正在生成合成数据并提取特征...")
    X, y = [], []
    for sweep_dict, label in build_sweeps(n_per_class=50):
        feats = extract_features(sweep_dict["freqs"], sweep_dict["mag_db"], sweep_dict["phase_deg"])
        X.append(to_vector(feats))
        y.append(label)
        
    X = np.array(X)
    y = np.array(y)
    print(f"   训练集生成完毕！共 {X.shape[0]} 个合成样本，{X.shape[1]} 个特征。")

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
    print(classification_report(y_test, mlp_preds, target_names=list(CLASSES.values())))
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
    cm = confusion_matrix(y_test, mlp_preds)
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=[CLASSES[c] for c in np.unique(y_test)])
    fig, ax = plt.subplots(figsize=(8, 6))
    disp.plot(cmap=plt.cm.Blues, ax=ax)
    plt.title("MLP Confusion Matrix (Synthetic Data)")
    plt.savefig(report_dir / "confusion_matrix.png", bbox_inches='tight')
    plt.close()

    # ----------------- 域外测试 (P0-1 核心逻辑) -----------------
    print("\n5. 正在使用外部数据测试 (Out-of-Distribution)...")
    is_dry_run = True
    real_test_result = "未找到 CSV 文件。"

    if os.path.exists(args.real_csv):
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
        title_type = "实测检验" if not is_dry_run else "dry-run 仿真（合成自检）"
        f.write(f"# ML 训练报告 ({title_type})\n\n")
        
        f.write("## 1. 数据来源与训练策略\n")
        f.write("- **训练集**: 100% 纯合成数据 (通过 `gen_mock.py` 物理公式生成)。\n")
        f.write("- **测试集**: 从合成数据中划分的 20% 数据。\n")
        f.write("- **域外测试 (OOD)**: 必须显式传入 `--real` 且文件存在，才认定为真实硬件采集的 CSV，防止用仿真数据伪造实测。\n\n")
        
        f.write("## 2. 模型性能 (基于合成数据)\n")
        f.write(f"- **决策树 (DT) 准确率**: {dt_acc * 100:.2f}%\n")
        f.write(f"- **神经网络 (MLP) 准确率**: {mlp_acc * 100:.2f}%\n")
        
        f.write("> *注：由于训练集和测试集均来自高度理想的合成数据，模型极易达到极高准确率。该数字仅反映算法在合成数据上的可分性，不代表真实硬件环境下的泛化表现。真实表现请参考下文的域外测试结果。*\n\n")
        
        f.write("### 混淆矩阵\n")
        f.write("![Confusion Matrix](./confusion_matrix.png)\n\n")
        
        f.write("## 3. 外部数据测试结果\n")
        f.write(real_test_result + "\n")
        
    print(f"\n 训练与评估完成！报告已保存至 {report_path}")
    if is_dry_run:
        print(" 当前为 dry-run 仿真测试，若要声明为真实实测，请添加 --real 参数。")

if __name__ == "__main__":
    main()