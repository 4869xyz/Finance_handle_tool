export type BatchStatus = 'UPLOADED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

export type SourceFolder =
  | 'INVOICE'
  | 'REPLACEMENT_INVOICE'
  | 'PAYMENT_EVIDENCE';

export type DocumentType =
  | 'VAT_INVOICE'
  | 'ELECTRONIC_INVOICE'
  | 'TAXI_INVOICE'
  | 'ITINERARY'
  | 'TRAIN_OR_AIR_TICKET'
  | 'PAYMENT_SCREENSHOT'
  | 'ORDER_SCREENSHOT'
  | 'OVERSEAS_INVOICE_OR_RECEIPT'
  | 'RECEIPT'
  | 'OTHER_DOCUMENT'
  | 'UNKNOWN'
  | 'VIDEO';

export type ProcessingStatus =
  | 'UPLOADED'
  | 'PROCESSING'
  | 'RECOGNIZED'
  | 'NEEDS_MANUAL_REVIEW'
  | 'SKIPPED'
  | 'FAILED';

export interface ProcessingBatch {
  id: number;
  employee_name: string;
  expense_month: string;
  status: BatchStatus;
  status_reason: string | null;
  total_files: number;
  recognized_files: number;
  manual_review_files: number;
  skipped_files: number;
  failed_files: number;
  created_at: string;
  updated_at: string;
}

export interface BatchDetail extends ProcessingBatch {
  source_counts: Record<string, number>;
  status_counts: Record<string, number>;
}

export interface BatchListResponse {
  items: ProcessingBatch[];
  total: number;
  page: number;
  page_size: number;
}

export interface SourceDocument {
  id: number;
  batch_id: number;
  source_folder: SourceFolder;
  relative_path: string;
  file_name: string;
  file_type: string;
  file_size: number;
  file_hash: string;
  document_type: DocumentType;
  processing_status: ProcessingStatus;
  status_reason: string | null;
  status_detail: string | null;
  raw_text: string | null;
  classification_confidence: string | null;
  extraction_confidence: string | null;
  document_date: string | null;
  counterparty: string | null;
  description: string | null;
    amount: string | null;
  invoice_number: string | null;
  transaction_number: string | null;
  item_name: string | null;
  order_number: string | null;
  trip_origin: string | null;
  trip_destination: string | null;
  trip_time_text: string | null;
  extracted_fields_json: Record<string, unknown> | null;
  classification_raw_response: string | null;
  extraction_raw_response: string | null;
  model_provider: string | null;
  model_name: string | null;
  processing_started_at: string | null;
  processing_attempts: number;
  created_at: string;
  updated_at: string;
}

export interface SourceDocumentListResponse {
  items: SourceDocument[];
  total: number;
  page: number;
  page_size: number;
}

export interface BatchUploadItem {
  file: File;
  relativePath: string;
}

export interface ReprocessResponse {
  batch_id: number;
  scheduled_count: number;
  message: string;
}

export interface ReconcileResponse {
  batch_id: number;
  status: 'COMPLETED' | 'REVIEW_REQUIRED';
  total_records: number;
  confirmed_groups: number;
  review_groups: number;
  eligible_total: string | null;
  message: string;
}

export interface ReviewReference {
  id: number;
  batch_id: number;
  version: number;
  original_filename: string;
  file_hash: string;
  file_size: number;
  sheet_names: string[];
  row_count: number;
  created_at: string;
  is_current: boolean;
}

export interface ReviewReferenceListResponse {
  items: ReviewReference[];
}

export const batchStatusLabels: Record<BatchStatus, string> = {
  UPLOADED: '已上传',
  PROCESSING: '识别中',
  COMPLETED: '已完成',
  FAILED: '批次失败',
};

export const sourceFolderLabels: Record<SourceFolder, string> = {
  INVOICE: '发票',
  REPLACEMENT_INVOICE: '补票',
  PAYMENT_EVIDENCE: '支付凭证',
};

export const processingStatusLabels: Record<ProcessingStatus, string> = {
  UPLOADED: '待识别',
  PROCESSING: '识别中',
  RECOGNIZED: '已识别',
  NEEDS_MANUAL_REVIEW: '需人工复核',
  SKIPPED: '已跳过',
  FAILED: '处理失败',
};

export const documentTypeLabels: Record<DocumentType, string> = {
  VAT_INVOICE: '增值税发票',
  ELECTRONIC_INVOICE: '电子/数电发票',
  TAXI_INVOICE: '出租车发票',
  ITINERARY: '行程单',
  TRAIN_OR_AIR_TICKET: '火车/航空票',
  PAYMENT_SCREENSHOT: '支付凭证',
  ORDER_SCREENSHOT: '订单凭证',
  OVERSEAS_INVOICE_OR_RECEIPT: '境外发票/收据',
  RECEIPT: '收据/小票',
  OTHER_DOCUMENT: '其他资料',
  UNKNOWN: '待判定',
  VIDEO: '视频',
};

export const reasonLabels: Record<string, string> = {
  UNSUPPORTED_VIDEO: '视频文件暂不处理，需要人工处理',
  DUPLICATE_SUMMARY_PDF: '重复汇总PDF，已跳过',
  IMAGE_PIXEL_LIMIT_EXCEEDED: '图片像素过大，需要人工处理',
  MULTIPLE_TRANSACTIONS: '包含多笔交易且没有可信汇总金额，需要人工复核',
  INVALID_MODEL_RESPONSE: '模型返回格式无效，需要人工复核',
  LLM_UNAVAILABLE: '模型服务不可用，规则结果不足以自动通过',
  LOW_CLASSIFICATION_CONFIDENCE: '文档类型置信度不足',
  LOW_EXTRACTION_CONFIDENCE: '字段提取置信度不足',
  OCR_LLM_FIELD_CONFLICT: 'LLM与OCR/Python字段核对不一致',
  CALCULATED_AMOUNT_REVIEW: '模型计算金额需要人工复核',
  TRANSACTION_RECORD_REVIEW: '交易记录金额或明细验算需要人工复核',
  MISSING_CRITICAL_FIELDS: '缺少关键字段',
  UNKNOWN_DOCUMENT_TYPE: '无法可靠判断文档类型',
  PROCESSING_ERROR: '文档处理失败',
  PROCESS_INTERRUPTED: '服务重启导致处理任务中断，可重新处理',
  COORDINATOR_FAILED: '批次协调任务失败',
};
