# backend/app/services/

核心业务与识别能力。

## 成员

| 文件 | 职责 | 依赖 |
|------|------|------|
| `invoice_service.py` | 发票处理编排：并行 OCR+LLM、字段合并、待审核判定 | ocr/llm、models |
| `batch_service.py` | 批次路径/上传校验、原子登记、特殊规则、后台领取、统计、恢复与重试 | batch model、document_service |
| `package_ingest_service.py` | 报销提交包拆月、总表登记 | batch_service、claim_workbook_service |
| `claim_workbook_service.py` | 报销总表解析与「系统审核」列写回 | openpyxl |
| `claim_completeness_service.py` | 总表明细与识别结果缺件/分类核验 | expense_record、expense_classification |
| `document_service.py` | 多资料正文分类、按类型分流提取（PDF 坐标表格 / 规则优先 / 文本 LLM / vision 升级）、整单 OCR/Python 失败兜底、关键字段矩阵与公开字段映射 | ocr/llm、batch model |
| `document_prompts.py` | 通用报销资料分类与字段提取 Prompt 4.3（分类型规则 + 交易级 Schema + 展示字段） | document_service |
| `expense_record_service.py` | 将文件级 JSON 转换为筛选、分类和导出使用的交易级视图 | batch model |
| `audit_classification_service.py` | 审核筛选与费用分类的统一模型批处理、记录级 JSON 缓存、失效判断、重试和耗时指标 | expense record、llm、company profile、review reference |
| `document_reconciliation_service.py` | 批次跨文件候选生成、LLM 主辅凭证判断、Python 校验与 JSON 持久化 | expense record、llm、batch model |
| `expense_classification_service.py` | 月度资料费用分类、人民币单币种汇总、发票/支付金额核对与风险说明 | batch model、review reference、llm_service |
| `exclusion_service.py` | 月度资料保留、排除和待复核判定 | company profile、review reference、llm_service |
| `ocr_service.py` | PaddleOCR + 旧发票 PDF 判定 + 通用全页 PDF 文本层（含文字坐标） | paddleocr、pdfplumber 等 |
| `llm_service.py` | 多厂商 Vision LLM 调用与 JSON 解析 | prompts、config |
| `prompts.py` | 中国发票 Vision System Prompt / JSON Schema | 被 llm_service 使用 |
| `model_registry.py` | 设置页原生模型 ID 与能力描述（直接发给厂商 SDK） | settings 相关 |
| `audit_service.py` | 写审计日志 | audit_log model |

## 边界

- `invoice_service` 保持原增值税发票双源流程，不依赖新批次模型。
- `document_service` 面向第一阶段多资料类型；业务字段只来自正文，文件名/月份不能补值。
- 月度字段提取按类型分流：标准发票/出租车票/行程车票先走本地规则（发票复用 `FieldExtractor`），关键字段齐全且置信度达标则 `RULE_PRIMARY` 且不调提取 LLM。高德行程单优先用 PDF 原生坐标按表头归列并合并跨行起点/终点；文本规则次之；再升 LLM。行程单 `RULE_PRIMARY` 要求每条 TRIP 含可用起终点与开始时间，且记录数与“共计 N 单”一致。支付/订单/收据等 OCR 文本充足时仅文本 LLM（`LLM_TEXT`），OCR 稀疏或仍缺关键字段时升级 vision（`LLM_VISION`）。Prompt 4.3 强化多行程顺序、标题禁令与禁止伪造单段结束时间；多行程展示列按段用 `/` 有序拼接并保留重复地点。合法结果不做字段级规则混补，仅整单升级或整单 `OCR_RULE_FALLBACK`。`_validation` 记录 `final_source` 与 `escalation_path`。
- 文件名仅可作为 `description` 的最后兜底：去掉扩展名后最多保留 500 字符，并写入 `description_source=FILENAME_FALLBACK`；不得用于其他业务字段或模型推断。
- 批次完成后由 Python 生成保守的跨文件候选，核对模型只判断关系和主凭证；Python 校验引用、置信度与同组金额后写入既有 `_reconciliation` 供审计。分类汇总直接累加交易级识别金额，不使用核对后的建议计入金额。
- `audit_classification_service` 只对缓存失效的已识别交易调用模型；源记录内容、Prompt/策略、公司档案、辅助表或模型配置变化会使对应记录失效。普通导出不补跑缺失的跨文件核对，记录级视图会把该事实标记为 `NOT_RECONCILED` 供分类审计明细展示。
- 审核分类 Prompt 2.0 分别输出审核/分类复核标记，并记录分类证据来源；明确常见类别边界、置信度标尺和严格 JSON 检查。Python 确定性规则与辅助表强匹配优先，模型复核标记只影响对应的模型软判断。
- 数据库状态和统计属于 `batch_service`；OCR/LLM 层不得直接改批次账本。
- OCR worker 当前串行领取文档；`batch_processing_concurrency` 配置项尚未接入本 worker（另开改造）。
