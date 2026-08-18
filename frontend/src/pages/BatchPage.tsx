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
  auditReimbursementPackage,
  createDocumentBatch,
  createReimbursementPackage,
  exportFilteredDocumentBatch,
  exportPackageClaims,
  getCompanyProfile,
  getBatchReviewReferenceDownloadUrl,
  getDocumentBatch,
  getReimbursementPackage,
  listBatchDocuments,
  listDocumentBatches,
  listBatchReviewReferences,
  listReimbursementPackages,
  reconcileDocumentBatch,
  reprocessDocumentBatch,
  uploadBatchReviewReference,
} from '../services/api';
import type {
  BatchDetail,
  BatchUploadItem,
  ProcessingBatch,
  ReimbursementPackage,
  ReimbursementPackageDetail,
  ReviewReference,
  SourceDocument,
} from '../types/batch';
import {
  batchStatusLabels,
  documentTypeLabels,
  packageStatusLabels,
  processingStatusLabels,
  reasonLabels,
  sourceFolderLabels,
} from '../types/batch';
import styles from './BatchPage.module.css';


const MAX_FILE_SIZE = 150 * 1024 * 1024;
const MAX_TOTAL_SIZE = 256 * 1024 * 1024;
const MAX_FILES = 200;
const MAX_PACKAGE_FILES = 600;
const MAX_PACKAGE_TOTAL_SIZE = 768 * 1024 * 1024;
const MAX_REFERENCE_FILE_SIZE = 10 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = new Set([
  'pdf', 'jpg', 'jpeg', 'png', 'mp4', 'mov', 'avi', 'mkv',
]);
const PACKAGE_EXTRA_EXTENSIONS = new Set(['xlsx', 'txt']);
const TERMINAL_BATCH_STATUSES = new Set(['COMPLETED', 'FAILED']);
const TERMINAL_PACKAGE_STATUSES = new Set(['COMPLETED', 'FAILED']);
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


function validatePackagePath(path: string): boolean {
  const normalized = path.replace(/\\/g, '/');
  const parts = normalized.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..')) {
    return false;
  }
  const name = parts[parts.length - 1];
  const lower = name.toLowerCase();
  if (name.endsWith('报销总表.xlsx') || lower.endsWith('.txt') || lower.endsWith('.xlsx')) {
    return true;
  }
  const sources = new Set(['发票', '补票', '支付凭证']);
  return parts.some((part) => sources.has(part));
}


function looksLikeReimbursementPackage(paths: string[]): boolean {
  const sources = new Set(['发票', '补票', '支付凭证']);
  return paths.some((path) => {
    const normalized = path.replace(/\\/g, '/');
    if (normalized.endsWith('报销总表.xlsx')) {
      return true;
    }
    const parts = normalized.split('/');
    const sourceIndex = parts.findIndex((part) => sources.has(part));
    return sourceIndex >= 2;
  });
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
  const [reconciling, setReconciling] = useState(false);
  const [exportingFiltered, setExportingFiltered] = useState(false);
  const [uploadingReference, setUploadingReference] = useState(false);
  const [reviewReferences, setReviewReferences] = useState<ReviewReference[]>([]);
  const [packageFileList, setPackageFileList] = useState<UploadFile[]>([]);
  const [uploadingPackage, setUploadingPackage] = useState(false);
  const [recentPackages, setRecentPackages] = useState<ReimbursementPackage[]>([]);
  const [selectedPackage, setSelectedPackage] = useState<ReimbursementPackageDetail | null>(null);
  const [loadingPackage, setLoadingPackage] = useState(false);
  const [auditingPackage, setAuditingPackage] = useState(false);
  const [exportingPackage, setExportingPackage] = useState(false);
  const selectedPackageId = Number(searchParams.get('package') || 0) || null;

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
  const selectedPackageFiles = useMemo(
    () => packageFileList.flatMap((item) => (
      item.originFileObj ? [item.originFileObj] : []
    )),
    [packageFileList]
  );
  const selectedPackageTotalSize = useMemo(
    () => selectedPackageFiles.reduce((total, file) => total + file.size, 0),
    [selectedPackageFiles]
  );

  const refreshRecent = useCallback(async () => {
    const [batchResponse, packageResponse] = await Promise.all([
      listDocumentBatches(1, 10),
      listReimbursementPackages(1, 10),
    ]);
    setRecentBatches(batchResponse.items);
    setRecentPackages(packageResponse.items);
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

  useEffect(() => {
    if (!selectedPackageId) {
      setSelectedPackage(null);
      return;
    }
    setLoadingPackage(true);
    getReimbursementPackage(selectedPackageId)
      .then(setSelectedPackage)
      .catch((error) => message.error(errorMessage(error)))
      .finally(() => setLoadingPackage(false));
  }, [selectedPackageId]);

  useEffect(() => {
    if (
      !selectedPackageId
      || !selectedPackage
      || TERMINAL_PACKAGE_STATUSES.has(selectedPackage.status)
    ) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      Promise.all([
        getReimbursementPackage(selectedPackageId),
        refreshRecent(),
      ])
        .then(([detail]) => setSelectedPackage(detail))
        .catch((error) => message.error(errorMessage(error)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [refreshRecent, selectedPackage, selectedPackageId]);

  const handlePackageUpload = async () => {
    if (!selectedPackageFiles.length) {
      message.warning('请选择包含报销总表和月度发票/支付凭证目录的提交包');
      return;
    }
    const items: BatchUploadItem[] = selectedPackageFiles.map((file) => ({
      file,
      relativePath: browserRelativePath(file),
    }));
    if (items.some((item) => !validatePackagePath(item.relativePath))) {
      message.error('提交包内文件必须包含报销总表，且票据位于发票、补票或支付凭证目录下');
      return;
    }
    if (!items.some((item) => item.relativePath.replace(/\\/g, '/').endsWith('报销总表.xlsx'))) {
      message.error('提交包必须包含一份报销总表.xlsx');
      return;
    }
    setUploadingPackage(true);
    try {
      const created = await createReimbursementPackage(items);
      message.success(`提交包 #${created.id} 已登记，正在按月识别`);
      setPackageFileList([]);
      setSearchParams({ package: String(created.id) });
      await refreshRecent();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setUploadingPackage(false);
    }
  };

  const handlePackageAudit = async () => {
    if (!selectedPackageId) return;
    setAuditingPackage(true);
    try {
      const response = await auditReimbursementPackage(selectedPackageId);
      message.success(response.message);
      const detail = await getReimbursementPackage(selectedPackageId);
      setSelectedPackage(detail);
      await refreshRecent();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setAuditingPackage(false);
    }
  };

  const handlePackageExport = async () => {
    if (!selectedPackageId || !selectedPackage) return;
    setExportingPackage(true);
    try {
      const blob = await exportPackageClaims(selectedPackageId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${selectedPackage.employee_name}-报销总表-系统审核.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setExportingPackage(false);
    }
  };

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
    if (looksLikeReimbursementPackage(items.map((item) => item.relativePath))) {
      message.error('这是含总表的报销提交包，请使用页面上方「上传报销提交包」，不要用单月识别批次');
      return;
    }
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

  const handleReconcile = async () => {
    if (!selectedId || !batch || !TERMINAL_BATCH_STATUSES.has(batch.status)) return;
    setReconciling(true);
    try {
      const response = await reconcileDocumentBatch(selectedId);
      const detail = response.review_groups
        ? `，${response.review_groups} 个候选组需要人工确认`
        : '';
      message.info(`${response.message}${detail}`);
      await refreshSelected(selectedId);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setReconciling(false);
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
      message.success('审核及分类汇总表格已生成；浅红底异常记录和风险说明请人工复核');
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
      render: (value) => value != null ? `¥${Number(value).toFixed(2)}` : '-',
    },
    { title: '项目名称', dataIndex: 'item_name', width: 180, ellipsis: true, render: (v) => v || '-' },
    { title: '发票号', dataIndex: 'invoice_number', width: 180, render: (v) => v || '-' },
    { title: '交易/订单号', dataIndex: 'transaction_number', width: 180, render: (v) => v || '-' },
    { title: '订单号', dataIndex: 'order_number', width: 160, ellipsis: true, render: (v) => v || '-' },
    { title: '行程起点', dataIndex: 'trip_origin', width: 140, ellipsis: true, render: (v) => v || '-' },
    { title: '行程终点', dataIndex: 'trip_destination', width: 140, ellipsis: true, render: (v) => v || '-' },
    { title: '行程时间', dataIndex: 'trip_time_text', width: 180, ellipsis: true, render: (v) => v || '-' },
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
            含总表的「姓名-报销」文件夹请用提交包上传；单月目录仍可在下方单独建批次。
          </Text>
        </div>
      </header>

      <Card title="上传报销提交包">
        <Text type="secondary">
          请选择「马耿宾-报销」这类文件夹：内含报销总表.xlsx，以及各月发票、补票、支付凭证。不要填姓名/月份，也不要用下面的单月入口。
        </Text>
        <Dragger
          directory
          multiple
          accept=".pdf,.jpg,.jpeg,.png,.mp4,.mov,.avi,.mkv,.xlsx,.txt"
          fileList={packageFileList}
          showUploadList={false}
          beforeUpload={(file) => {
            const extension = file.name.split('.').pop()?.toLowerCase() || '';
            if (!ACCEPTED_EXTENSIONS.has(extension) && !PACKAGE_EXTRA_EXTENSIONS.has(extension)) {
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
            if (nextFiles.length > MAX_PACKAGE_FILES || total > MAX_PACKAGE_TOTAL_SIZE) {
              message.error('提交包最多 600 个文件且总大小不能超过 768 MiB');
              return;
            }
            setPackageFileList(nextList);
          }}
        >
          <p className="ant-upload-drag-icon"><FolderOpenOutlined /></p>
          <p className="ant-upload-text">点击或拖入报销提交包文件夹</p>
          <p className="ant-upload-hint">
            需包含报销总表.xlsx；票据仍须位于发票、补票或支付凭证目录下。
          </p>
        </Dragger>
        <div className={styles.uploadSummary}>
          <Text>
            已选择 {selectedPackageFiles.length} 个文件，
            {(selectedPackageTotalSize / 1024 / 1024).toFixed(2)} MiB
          </Text>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            loading={uploadingPackage}
            disabled={!selectedPackageFiles.length}
            onClick={handlePackageUpload}
          >
            上传提交包并开始识别
          </Button>
        </div>
      </Card>

      <Card title="新建识别批次（仅单月）">
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

      <Card title="最近提交包">
        <List
          dataSource={recentPackages}
          locale={{ emptyText: '暂无提交包' }}
          renderItem={(item) => (
            <List.Item
              className={selectedPackageId === item.id ? styles.selectedBatch : undefined}
              actions={[
                <Button
                  key="view"
                  type="link"
                  onClick={() => setSearchParams({ package: String(item.id) })}
                >
                  查看
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={`${item.employee_name} · 提交包 #${item.id}`}
                description={`${item.claim_count} 条总表明细 · ${dayjs(item.created_at).format('YYYY-MM-DD HH:mm')}`}
              />
              <Tag color={statusColor(item.status)}>{packageStatusLabels[item.status]}</Tag>
            </List.Item>
          )}
        />
      </Card>

      {selectedPackage ? (
        <Card
          loading={loadingPackage}
          title={`${selectedPackage.employee_name} · 提交包 #${selectedPackage.id}`}
          extra={(
            <Space wrap>
              <Tag color={statusColor(selectedPackage.status)}>
                {packageStatusLabels[selectedPackage.status]}
              </Tag>
              <Button
                icon={<ReloadOutlined />}
                loading={auditingPackage}
                disabled={!selectedPackage.batches.every((item) => TERMINAL_BATCH_STATUSES.has(item.status))}
                onClick={handlePackageAudit}
              >
                审核总表
              </Button>
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                loading={exportingPackage}
                disabled={!selectedPackage.audited_at}
                onClick={handlePackageExport}
              >
                下载系统审核总表
              </Button>
            </Space>
          )}
        >
          <Text type="secondary">
            {selectedPackage.original_filename} · {selectedPackage.claim_count} 条明细
            {selectedPackage.months.length ? ` · ${selectedPackage.months.join('、')}` : ''}
          </Text>
          <List
            size="small"
            dataSource={selectedPackage.batches}
            locale={{ emptyText: '暂无月份批次' }}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Button
                    key="batch"
                    type="link"
                    onClick={() => {
                      setDocumentPage(1);
                      setSearchParams({ batch: String(item.id) });
                    }}
                  >
                    查看识别批次
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={`${item.employee_name} · ${item.expense_month} · 批次 #${item.id}`}
                  description={`${item.total_files} 个文件`}
                />
                <Tag color={statusColor(item.status)}>{batchStatusLabels[item.status]}</Tag>
              </List.Item>
            )}
          />
        </Card>
      ) : null}

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
                      ? '保留全部资料并生成发票、支付凭证分类汇总；浅红底异常记录和风险说明供人工审核'
                      : '请等待当前批次处理结束后再生成'
                  }
                  onClick={handleFilteredExport}
                >
                  {exportingFiltered ? '正在生成审核及分类汇总表格…' : '生成审核及分类汇总表格'}
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  loading={reconciling}
                  disabled={!TERMINAL_BATCH_STATUSES.has(batch.status)}
                  onClick={handleReconcile}
                >
                  重新核对主辅凭证
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
