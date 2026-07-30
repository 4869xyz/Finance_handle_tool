import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Button,
  Card,
  DatePicker,
  Empty,
  Input,
  List,
  message,
  Progress,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  DownloadOutlined,
  FilterOutlined,
  FolderOpenOutlined,
  ReloadOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import axios from 'axios';
import dayjs, { Dayjs } from 'dayjs';
import type { UploadFile } from 'antd/es/upload/interface';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';

import {
  createDocumentBatch,
  exportFilteredDocumentBatch,
  getCompanyProfile,
  getBatchReviewReferenceDownloadUrl,
  getDocumentBatch,
  listBatchDocuments,
  listDocumentBatches,
  listBatchReviewReferences,
  reprocessDocumentBatch,
  uploadBatchReviewReference,
} from '../services/api';
import type {
  BatchDetail,
  BatchUploadItem,
  ProcessingBatch,
  ReviewReference,
  SourceDocument,
} from '../types/batch';
import {
  batchStatusLabels,
  documentTypeLabels,
  processingStatusLabels,
  reasonLabels,
  sourceFolderLabels,
} from '../types/batch';
import styles from './BatchPage.module.css';


const MAX_FILE_SIZE = 150 * 1024 * 1024;
const MAX_TOTAL_SIZE = 256 * 1024 * 1024;
const MAX_FILES = 200;
const MAX_REFERENCE_FILE_SIZE = 10 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = new Set([
  'pdf', 'jpg', 'jpeg', 'png', 'mp4', 'mov', 'avi', 'mkv',
]);
const TERMINAL_BATCH_STATUSES = new Set(['COMPLETED', 'FAILED']);
const { Dragger } = Upload;
const { Text, Title } = Typography;


function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') {
      return detail;
    }
  }
  return '请求失败，请稍后重试';
}


function browserRelativePath(file: File): string {
  return file.webkitRelativePath || file.name;
}


function validateClientPath(path: string): boolean {
  const normalized = path.replace(/\\/g, '/');
  const parts = normalized.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..')) {
    return false;
  }
  const sources = new Set(['发票', '补票', '支付凭证']);
  return sources.has(parts[0]) || (parts.length >= 3 && sources.has(parts[1]));
}


function statusColor(status: string): string {
  return {
    UPLOADED: 'default',
    PROCESSING: 'processing',
    COMPLETED: 'success',
    RECOGNIZED: 'success',
    NEEDS_MANUAL_REVIEW: 'warning',
    SKIPPED: 'default',
    FAILED: 'error',
  }[status] || 'default';
}


function safeFilteredFilename(batch: BatchDetail): string {
  const employee = batch.employee_name
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/^[ .]+|[ .]+$/g, '') || '员工';
  return `${employee}_${batch.expense_month}_剔除结果.xlsx`;
}


export default function BatchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = Number(searchParams.get('batch') || 0) || null;
  const [employeeName, setEmployeeName] = useState('');
  const [expenseMonth, setExpenseMonth] = useState<Dayjs | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [recentBatches, setRecentBatches] = useState<ProcessingBatch[]>([]);
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [documentsTotal, setDocumentsTotal] = useState(0);
  const [documentPage, setDocumentPage] = useState(1);
  const [documentPageSize, setDocumentPageSize] = useState(20);
  const [loadingBatch, setLoadingBatch] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [exportingFiltered, setExportingFiltered] = useState(false);
  const [uploadingReference, setUploadingReference] = useState(false);
  const [reviewReferences, setReviewReferences] = useState<ReviewReference[]>([]);

  const selectedFiles = useMemo(
    () => fileList.flatMap((item) => (
      item.originFileObj ? [item.originFileObj] : []
    )),
    [fileList]
  );
  const selectedTotalSize = useMemo(
    () => selectedFiles.reduce((total, file) => total + file.size, 0),
    [selectedFiles]
  );

  const refreshRecent = useCallback(async () => {
    const response = await listDocumentBatches(1, 10);
    setRecentBatches(response.items);
  }, []);

  const refreshSelected = useCallback(async (
    id: number,
    page = documentPage,
    pageSize = documentPageSize
  ) => {
    const [batchResponse, documentResponse, referenceResponse] = await Promise.all([
      getDocumentBatch(id),
      listBatchDocuments(id, page, pageSize),
      listBatchReviewReferences(id),
    ]);
    setBatch(batchResponse);
    setDocuments(documentResponse.items);
    setDocumentsTotal(documentResponse.total);
    setReviewReferences(referenceResponse.items);
  }, [documentPage, documentPageSize]);

  useEffect(() => {
    refreshRecent().catch((error) => message.error(errorMessage(error)));
  }, [refreshRecent]);

  useEffect(() => {
    if (!selectedId) {
      setBatch(null);
      setDocuments([]);
      setDocumentsTotal(0);
      setReviewReferences([]);
      return;
    }
    setLoadingBatch(true);
    refreshSelected(selectedId)
      .catch((error) => message.error(errorMessage(error)))
      .finally(() => setLoadingBatch(false));
  }, [selectedId, refreshSelected]);

  useEffect(() => {
    if (!selectedId || !batch || TERMINAL_BATCH_STATUSES.has(batch.status)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      Promise.all([
        refreshSelected(selectedId),
        refreshRecent(),
      ]).catch((error) => message.error(errorMessage(error)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [batch, refreshRecent, refreshSelected, selectedId]);

  const handleUpload = async () => {
    if (!employeeName.trim() || !expenseMonth) {
      message.warning('请填写员工姓名和报销月份');
      return;
    }
    if (!selectedFiles.length) {
      message.warning('请选择包含发票、补票或支付凭证目录的月度文件夹');
      return;
    }
    const items: BatchUploadItem[] = selectedFiles.map((file) => ({
      file,
      relativePath: browserRelativePath(file),
    }));
    if (items.some((item) => !validateClientPath(item.relativePath))) {
      message.error('目录中存在未知来源；文件必须位于发票、补票或支付凭证目录下');
      return;
    }
    setUploading(true);
    try {
      const created = await createDocumentBatch(
        employeeName.trim(),
        expenseMonth.format('YYYY-MM'),
        items
      );
      message.success(`批次 #${created.id} 已登记，正在后台识别`);
      setFileList([]);
      setDocumentPage(1);
      setSearchParams({ batch: String(created.id) });
      await refreshRecent();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setUploading(false);
    }
  };

  const handleReprocess = async () => {
    if (!selectedId) return;
    setReprocessing(true);
    try {
      const response = await reprocessDocumentBatch(selectedId);
      message.info(response.message);
      await refreshSelected(selectedId);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setReprocessing(false);
    }
  };

  const handleExport = () => {
    if (!selectedId || !batch || !TERMINAL_BATCH_STATUSES.has(batch.status)) return;
    window.open(`/api/batches/${selectedId}/export/excel`, '_blank', 'noopener,noreferrer');
  };

  const handleFilteredExport = async () => {
    if (!selectedId || !batch || !TERMINAL_BATCH_STATUSES.has(batch.status)) return;
    setExportingFiltered(true);
    try {
      const profile = await getCompanyProfile();
      if (!profile) {
        message.warning('尚未配置公司档案；发票将按放宽规则保留，并记录抬头无法核验提示');
      }
      const blob = await exportFilteredDocumentBatch(selectedId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = safeFilteredFilename(batch);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success('审核及分类汇总表格已生成；红字和金额差额请人工复核');
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setExportingFiltered(false);
    }
  };

  const handleReferenceUpload = async (file: File) => {
    if (!selectedId) return;
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      message.error('报销明细参考表仅支持 .xlsx 文件');
      return;
    }
    if (file.size > MAX_REFERENCE_FILE_SIZE) {
      message.error('报销明细参考表不能超过 10 MB');
      return;
    }
    setUploadingReference(true);
    try {
      const uploaded = await uploadBatchReviewReference(selectedId, file);
      message.success(`参考表 v${uploaded.version} 上传成功，解析 ${uploaded.row_count} 行`);
      const response = await listBatchReviewReferences(selectedId);
      setReviewReferences(response.items);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setUploadingReference(false);
    }
  };

  const handleTableChange = (pagination: TablePaginationConfig) => {
    const page = pagination.current || 1;
    const pageSize = pagination.pageSize || 20;
    setDocumentPage(page);
    setDocumentPageSize(pageSize);
    if (selectedId) {
      refreshSelected(selectedId, page, pageSize)
        .catch((error) => message.error(errorMessage(error)));
    }
  };

  const columns: ColumnsType<SourceDocument> = [
    {
      title: '来源',
      dataIndex: 'source_folder',
      width: 100,
      render: (value) => sourceFolderLabels[value as SourceDocument['source_folder']],
    },
    {
      title: '文件',
      dataIndex: 'file_name',
      width: 220,
      ellipsis: true,
      render: (value, record) => (
        <Space direction="vertical" size={0}>
          <Text>{value}</Text>
          <Text type="secondary" className={styles.pathText}>{record.relative_path}</Text>
        </Space>
      ),
    },
    {
      title: '资料类型',
      dataIndex: 'document_type',
      width: 150,
      render: (value) => documentTypeLabels[value as SourceDocument['document_type']],
    },
    {
      title: '处理状态',
      dataIndex: 'processing_status',
      width: 130,
      render: (value) => (
        <Tag color={statusColor(value)}>
          {processingStatusLabels[value as SourceDocument['processing_status']]}
        </Tag>
      ),
    },
    {
      title: '原因',
      dataIndex: 'status_reason',
      width: 260,
      render: (value, record) => (
        <Text title={record.status_detail || undefined}>
          {value ? reasonLabels[value] || record.status_detail || value : '-'}
        </Text>
      ),
    },
    { title: '日期', dataIndex: 'document_date', width: 120, render: (v) => v || '-' },
    { title: '对方', dataIndex: 'counterparty', width: 180, ellipsis: true, render: (v) => v || '-' },
    { title: '摘要', dataIndex: 'description', width: 220, ellipsis: true, render: (v) => v || '-' },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 130,
      align: 'right',
      render: (value, record) => value ? `${record.currency || ''} ${value}`.trim() : '-',
    },
    { title: '发票号', dataIndex: 'invoice_number', width: 180, render: (v) => v || '-' },
    { title: '交易/订单号', dataIndex: 'transaction_number', width: 180, render: (v) => v || '-' },
    {
      title: '提取置信度',
      dataIndex: 'extraction_confidence',
      width: 120,
      render: (value) => value ? `${(Number(value) * 100).toFixed(1)}%` : '-',
    },
  ];

  const terminalCount = batch
    ? batch.recognized_files
      + batch.manual_review_files
      + batch.skipped_files
      + batch.failed_files
    : 0;
  const progress = batch?.total_files
    ? Math.round((terminalCount / batch.total_files) * 100)
    : 0;

  return (
    <div className={styles.pageContainer}>
      <header className={styles.pageHeader}>
        <div>
          <Title level={1} className={styles.pageTitle}>月度资料识别</Title>
          <Text type="secondary">
            选择员工月度目录，统一登记发票、补票和支付凭证并跟踪识别结果。
          </Text>
        </div>
      </header>

      <Card title="新建识别批次">
        <div className={styles.formRow}>
          <Input
            value={employeeName}
            onChange={(event) => setEmployeeName(event.target.value)}
            placeholder="员工姓名"
            maxLength={100}
          />
          <DatePicker
            picker="month"
            value={expenseMonth}
            onChange={setExpenseMonth}
            placeholder="报销月份"
            allowClear
          />
        </div>
        <Dragger
          directory
          multiple
          accept=".pdf,.jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv"
          fileList={fileList}
          showUploadList={false}
          beforeUpload={(file) => {
            const extension = file.name.split('.').pop()?.toLowerCase() || '';
            if (!ACCEPTED_EXTENSIONS.has(extension)) {
              message.error(`不支持的文件类型：${file.name}`);
              return Upload.LIST_IGNORE;
            }
            if (file.size > MAX_FILE_SIZE) {
              message.error(`单个文件不能超过 150 MiB：${file.name}`);
              return Upload.LIST_IGNORE;
            }
            return false;
          }}
          onChange={({ fileList: nextList }) => {
            const nextFiles = nextList.flatMap((item) => (
              item.originFileObj ? [item.originFileObj] : []
            ));
            const total = nextFiles.reduce((sum, file) => sum + file.size, 0);
            if (nextFiles.length > MAX_FILES || total > MAX_TOTAL_SIZE) {
              message.error('单批最多 200 个文件且总大小不能超过 256 MiB');
              return;
            }
            setFileList(nextList);
          }}
        >
          <p className="ant-upload-drag-icon"><FolderOpenOutlined /></p>
          <p className="ant-upload-text">点击或拖入月度文件夹</p>
          <p className="ant-upload-hint">
            支持 PDF、JPG、PNG 和常见视频格式；后端会再次执行权威校验。
          </p>
        </Dragger>
        <div className={styles.uploadSummary}>
          <Text>
            已选择 {selectedFiles.length} 个文件，
            {(selectedTotalSize / 1024 / 1024).toFixed(2)} MiB
          </Text>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            loading={uploading}
            disabled={!selectedFiles.length}
            onClick={handleUpload}
          >
            创建批次并开始识别
          </Button>
        </div>
      </Card>

      <Card title="最近批次">
        <List
          dataSource={recentBatches}
          locale={{ emptyText: '暂无批次' }}
          renderItem={(item) => (
            <List.Item
              className={selectedId === item.id ? styles.selectedBatch : undefined}
              actions={[
                <Button
                  key="view"
                  type="link"
                  onClick={() => {
                    setDocumentPage(1);
                    setSearchParams({ batch: String(item.id) });
                  }}
                >
                  查看
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={`${item.employee_name} · ${item.expense_month} · 批次 #${item.id}`}
                description={`${item.total_files} 个文件 · ${dayjs(item.created_at).format('YYYY-MM-DD HH:mm')}`}
              />
              <Tag color={statusColor(item.status)}>{batchStatusLabels[item.status]}</Tag>
            </List.Item>
          )}
        />
      </Card>

      {batch ? (
        <>
          <Card
            loading={loadingBatch}
            title={`${batch.employee_name} · ${batch.expense_month} · 批次 #${batch.id}`}
            extra={(
              <Space wrap className={styles.batchActions}>
                <Tag color={statusColor(batch.status)}>{batchStatusLabels[batch.status]}</Tag>
                <Button
                  icon={<DownloadOutlined />}
                  disabled={!TERMINAL_BATCH_STATUSES.has(batch.status)}
                  title={
                    TERMINAL_BATCH_STATUSES.has(batch.status)
                      ? '导出当前批次全部文档'
                      : '请等待当前批次处理结束后再导出'
                  }
                  onClick={handleExport}
                >
                  导出 Excel
                </Button>
                <Button
                  type="primary"
                  icon={<FilterOutlined />}
                  loading={exportingFiltered}
                  disabled={!TERMINAL_BATCH_STATUSES.has(batch.status)}
                  title={
                    TERMINAL_BATCH_STATUSES.has(batch.status)
                      ? '保留全部资料并生成发票、支付凭证分类汇总；红字和金额差额供人工审核'
                      : '请等待当前批次处理结束后再生成'
                  }
                  onClick={handleFilteredExport}
                >
                  生成审核及分类汇总表格
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  loading={reprocessing}
                  onClick={handleReprocess}
                >
                  重新处理可重试文档
                </Button>
              </Space>
            )}
          >
            <Progress percent={progress} status={batch.status === 'FAILED' ? 'exception' : 'active'} />
            <div className={styles.metrics}>
              <Statistic title="总文件" value={batch.total_files} />
              <Statistic title="已识别" value={batch.recognized_files} />
              <Statistic title="需人工复核" value={batch.manual_review_files} />
              <Statistic title="已跳过" value={batch.skipped_files} />
              <Statistic title="失败" value={batch.failed_files} />
            </div>
          </Card>

          <Card
            title="报销明细参考表"
            extra={(
              <Upload
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                showUploadList={false}
                beforeUpload={(file) => {
                  void handleReferenceUpload(file);
                  return false;
                }}
              >
                <Button icon={<UploadOutlined />} loading={uploadingReference}>
                  上传参考表
                </Button>
              </Upload>
            )}
          >
            {reviewReferences.length ? (
              <List
                size="small"
                dataSource={reviewReferences}
                renderItem={(reference) => (
                  <List.Item
                    actions={[
                      <Button
                        key="download"
                        type="link"
                        icon={<DownloadOutlined />}
                        onClick={() => window.open(
                          getBatchReviewReferenceDownloadUrl(batch.id, reference.version),
                          '_blank',
                          'noopener,noreferrer'
                        )}
                      >
                        下载
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={(
                        <Space wrap>
                          <Text>{reference.original_filename}</Text>
                          <Tag color={reference.is_current ? 'green' : 'default'}>
                            v{reference.version}{reference.is_current ? ' · 当前生效' : ''}
                          </Tag>
                        </Space>
                      )}
                      description={`${reference.row_count} 个有效行 · ${reference.sheet_names.length} 个工作表 · ${dayjs(reference.created_at).format('YYYY-MM-DD HH:mm')}`}
                    />
                  </List.Item>
                )}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未上传；不影响生成审核及分类汇总表格" />
            )}
          </Card>

          <Card title="文档明细">
            <Table
              rowKey="id"
              columns={columns}
              dataSource={documents}
              loading={loadingBatch}
              scroll={{ x: 1900 }}
              pagination={{
                current: documentPage,
                pageSize: documentPageSize,
                total: documentsTotal,
                showSizeChanger: true,
                showTotal: (total) => `共 ${total} 条`,
              }}
              onChange={handleTableChange}
            />
          </Card>
        </>
      ) : (
        <Card><Empty description="选择一个批次查看识别进度和文档明细" /></Card>
      )}
    </div>
  );
}
