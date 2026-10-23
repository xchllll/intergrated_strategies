import sys
import os
from pathlib import Path
import numpy as np
import joblib

# 把项目根目录加入环境变量，这样才能导入 common 里的模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.features import extract_features, to_vector
from ml.gen_mock import build_sweeps, CLASSES
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, accuracy_score
from sklearn.pipeline import make_pipeline

def main():
    print("1. 正在生成假数据并提取特征 (这可能需要几秒钟)...")
    X = []
    y = []
    
    # 遍历生成器，提取特征
    for sweep_dict, label in build_sweeps(n_per_class=50): # 每类生成 50 组数据
        feats = extract_features(sweep_dict["freqs"], sweep_dict["mag_db"], sweep_dict["phase_deg"])
        X.append(to_vector(feats))
        y.append(label)
        
    X = np.array(X)
    y = np.array(y)
    print(f"   数据准备完毕！共提取了 {X.shape[0]} 个样本，每个样本 {X.shape[1]} 个特征。")

    # 划分训练集 (80%) 和测试集 (20%)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    # 创建 model 文件夹（如果没有的话）
    os.makedirs("model", exist_ok=True)

    # ----------------- 训练决策树 -----------------
    print("\n2. 正在训练 决策树 (Decision Tree)...")
    # 使用 Pipeline，先做标准化 (StandardScaler) 再分类，防止特征量纲差异过大
    dt_pipeline = make_pipeline(StandardScaler(), DecisionTreeClassifier(max_depth=6, random_state=42))
    dt_pipeline.fit(X_train, y_train)
    dt_preds = dt_pipeline.predict(X_test)
    
    print(f"   决策树测试集准确率: {accuracy_score(y_test, dt_preds) * 100:.2f}%")
    joblib.dump(dt_pipeline, "model/topo_dt.joblib")

    # ----------------- 训练轻量神经网络 -----------------
    print("\n3. 正在训练 轻量神经网络 (MLP)...")
    mlp_pipeline = make_pipeline(
        StandardScaler(), 
        MLPClassifier(hidden_layer_sizes=(32, 16), max_iter=2000, random_state=42)
    )
    mlp_pipeline.fit(X_train, y_train)
    mlp_preds = mlp_pipeline.predict(X_test)
    
    print(f"   神经网络测试集准确率: {accuracy_score(y_test, mlp_preds) * 100:.2f}%")
    print("\n   神经网络详细分类报告:")
    print(classification_report(y_test, mlp_preds, target_names=list(CLASSES.values())))
    
    joblib.dump(mlp_pipeline, "model/topo_mlp.joblib")
    print("模型已保存至 ml/model/ 目录下。")

if __name__ == "__main__":
    main()