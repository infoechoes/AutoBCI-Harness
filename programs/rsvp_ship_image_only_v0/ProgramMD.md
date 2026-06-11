# ProgramMD: rsvp_ship_image_only_v0

- version: 0.1
- status: frozen
- task_type: image_binary_classification
- primary_metric: test_balanced_accuracy

## 研究目标
先做纯图像 ship / not-ship 二分类，不使用脑电。

## 数据边界
- dataset: Downloads/RSVP跨模态数据
- input_mode: image_only
- raw_data_policy: read_only
- train sessions: rsvp_ship_image_train_manifest
- val sessions: rsvp_ship_image_val_manifest
- test sessions: rsvp_ship_image_test_manifest

## 标签定义与风险
- source: folder_labels: target=ship, nontarget=non-ship/background
- known risks: 当前只冻结纯图像任务，不能据此声称脑电路线效果；图像分类可能利用低层亮度、颜色或构图线索；需要审计图片标签、重复样本和数据划分泄漏。
- acceptance note: 本契约只允许纯图像 ship / not-ship 二分类；脑电比较必须另开或修订 ProgramMD。

## 搜索空间
- windows_seconds: [0]
- lags_ms: [0]
- model_families: image_logistic_baseline, image_hog_linear_probe, image_color_histogram_logistic, image_tiny_cnn_probe, image_transfer_embedding
- feature_families: image_pixels_or_embeddings

## 指标
- primary: test_balanced_accuracy
- secondary: val_balanced_accuracy, macro_f1, per_class_recall, confusion_matrix
- minimum_report_fields: dataset_audit, per_split_counts, image_metrics, artifact_paths

## 禁区
- change_task_type
- change_primary_metric
- change_split_without_amendment
- modify_downloads_source_data
- read_eeg_for_image_only_program
- claim_eeg_vs_image_comparison
- overwrite_existing_result
- start_executor_without_user_approval

## 不确定性
- 下载目录仍需审计：纯图像任务只确认图片和标签，不读取脑电文件。
- 如果后续要比较脑电，需要同一刺激序列或可追溯事件表，不能直接拿不同来源结果相减。
