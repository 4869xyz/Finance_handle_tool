import { useState, useEffect } from 'react';
import {
  Modal,
  Form,
  Select,
  Input,
  Button,
  Space,
  message,
  Alert,
  Spin,
  Tag,
  Divider,
  Tooltip,
  AutoComplete,
} from 'antd';
import {
  SettingOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ApiOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import { getLLMStatus, configureLLM, testLLMConfig, getAvailableModels } from '../services/api';
import type { LLMStatusResponse, LLMProviderInfo, ModelInfo } from '../types/invoice';

interface LLMConfigModalProps {
  open: boolean;
  onClose: () => void;
  onConfigured?: () => void;
}

const FALLBACK_MODELS: Record<string, ModelInfo[]> = {
  openai: [
    { id: 'gpt-4o', name: 'GPT-4o', vision: true },
    { id: 'gpt-4o-mini', name: 'GPT-4o Mini', vision: true },
    { id: 'gpt-4-turbo', name: 'GPT-4 Turbo', vision: true },
  ],
  anthropic: [
    { id: 'claude-sonnet-4-20250514', name: 'Claude Sonnet 4', vision: true },
    { id: 'claude-3-5-sonnet-20241022', name: 'Claude 3.5 Sonnet', vision: true },
    { id: 'claude-3-5-haiku-20241022', name: 'Claude 3.5 Haiku', vision: true },
  ],
  google: [
    { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash', vision: true },
    { id: 'gemini-1.5-pro', name: 'Gemini 1.5 Pro', vision: true },
    { id: 'gemini-1.5-flash', name: 'Gemini 1.5 Flash', vision: true },
  ],
  qwen: [
    { id: 'qwen3.7-plus', name: 'Qwen 3.7 Plus', vision: true },
    { id: 'qwen3-vl-plus', name: 'Qwen 3 VL Plus', vision: true },
    { id: 'qwen3-vl-flash', name: 'Qwen 3 VL Flash', vision: true },
    { id: 'qwen3.5-ocr', name: 'Qwen 3.5 OCR', vision: true },
    { id: 'qwen2.5-vl-72b-instruct', name: 'Qwen 2.5 VL 72B', vision: true },
    { id: 'qwen-turbo', name: 'Qwen Turbo', vision: false },
  ],
  deepseek: [
    { id: 'deepseek-chat', name: 'DeepSeek Chat', vision: false },
    { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner', vision: false },
  ],
  zhipu: [
    { id: 'glm-4v', name: 'GLM-4V', vision: true },
    { id: 'glm-4-flash', name: 'GLM-4 Flash', vision: false },
  ],
};

function LLMConfigModal({ open, onClose, onConfigured }: LLMConfigModalProps) {
  const [loading, setLoading] = useState(true);
  const [testingConfig, setTestingConfig] = useState(false);
  const [configuring, setConfiguring] = useState(false);
  const [status, setStatus] = useState<LLMStatusResponse | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [form] = Form.useForm();
  const watchedModel = Form.useWatch('model', form);

  const selectedInfo = status?.available_providers.find((item) => item.name === selectedProvider);
  const providerHasSavedKey = Boolean(selectedInfo?.is_configured);
  const selectedModelInfo = models.find((item) => item.id === watchedModel);
  const showVisionWarning =
    selectedProvider === 'deepseek' || selectedModelInfo?.vision === false;

  const fetchModels = async (provider: string) => {
    setModelsLoading(true);
    try {
      const response = await getAvailableModels(provider, false);
      const nextModels =
        response.models && response.models.length > 0
          ? response.models
          : FALLBACK_MODELS[provider] || [];
      setModels(nextModels);
      return nextModels;
    } catch (error) {
      console.error('Failed to fetch models:', error);
      const nextModels = FALLBACK_MODELS[provider] || [];
      setModels(nextModels);
      return nextModels;
    } finally {
      setModelsLoading(false);
    }
  };

  const applyProviderFields = (
    provider: string,
    data: LLMStatusResponse | null,
    providerModels: ModelInfo[],
  ) => {
    const savedProvider = data?.available_providers.find((item) => item.name === provider);
    const savedModel =
      (data?.active_provider === provider ? data.active_model : undefined) ||
      savedProvider?.model ||
      providerModels[0]?.id ||
      '';
    form.setFieldsValue({
      provider,
      model: savedModel,
      api_key: '',
      base_url: savedProvider?.base_url || '',
      config_token: '',
    });
  };

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const data = await getLLMStatus();
      setStatus(data);
      const provider = data.active_provider || '';
      setSelectedProvider(provider);
      if (provider) {
        const providerModels = await fetchModels(provider);
        applyProviderFields(provider, data, providerModels);
      } else {
        applyProviderFields(provider, data, []);
      }
    } catch (error) {
      console.error('Failed to fetch LLM status:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      fetchStatus();
    }
  }, [open]);

  useEffect(() => {
    if (!models.length || !selectedProvider) {
      return;
    }
    const currentModel = form.getFieldValue('model');
    if (!currentModel) {
      form.setFieldsValue({ model: models[0].id });
    }
  }, [models, selectedProvider, form]);

  const handleProviderChange = async (provider: string) => {
    setSelectedProvider(provider);
    const providerModels = await fetchModels(provider);
    applyProviderFields(provider, status, providerModels);
  };

  const handleConfigure = async () => {
    try {
      const values = await form.validateFields();
      setConfiguring(true);

      const config = {
        provider: values.provider,
        api_key: values.api_key?.trim() || undefined,
        model: values.model || undefined,
        base_url: values.base_url || undefined,
      };

      const configToken = values.config_token?.trim();
      const result = await configureLLM(config, configToken || undefined);
      if (result.success) {
        message.success(result.message);
        await fetchStatus();
        onConfigured?.();
      }
    } catch (error: unknown) {
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { data?: { detail?: string } } };
        if (axiosError.response?.data?.detail) {
          message.error(axiosError.response.data.detail);
          return;
        }
      }
      if (error && typeof error === 'object' && 'errorFields' in error) {
        return;
      }
      message.error('配置失败');
    } finally {
      setConfiguring(false);
    }
  };

  const handleTestConfig = async () => {
    const values = form.getFieldsValue();
    if (!values.provider) {
      message.warning('请先选择LLM提供商');
      return;
    }
    if (!values.api_key?.trim() && !providerHasSavedKey) {
      message.warning('请先输入API密钥');
      return;
    }
    setTestingConfig(true);
    try {
      const result = await testLLMConfig({
        provider: values.provider,
        api_key: values.api_key?.trim() || undefined,
        model: values.model || undefined,
        base_url: values.base_url?.trim() || undefined,
      });
      if (result.success) {
        message.success(`${result.message} (${result.response_time_ms}ms)`);
      } else {
        message.error(result.message);
      }
    } catch (error: unknown) {
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { data?: { detail?: string } } };
        if (axiosError.response?.data?.detail) {
          message.error(axiosError.response.data.detail);
          return;
        }
      }
      message.error('配置测试失败');
    } finally {
      setTestingConfig(false);
    }
  };

  const renderProviderStatus = (provider: LLMProviderInfo) => {
    const isActive = status?.active_provider === provider.name;
    return (
      <div key={provider.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>{provider.display_name}</span>
        {provider.is_configured && (
          <Tag color="success" icon={<CheckCircleOutlined />}>
            已配置
          </Tag>
        )}
        {isActive && (
          <Tag color="blue">当前使用</Tag>
        )}
        {provider.model && provider.is_configured && (
          <Tag>{provider.model}</Tag>
        )}
      </div>
    );
  };

  return (
    <Modal
      title={
        <Space>
          <SettingOutlined />
          <span>LLM 服务配置</span>
        </Space>
      }
      open={open}
      onCancel={onClose}
      width={600}
      footer={
        loading ? null : (
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button onClick={handleTestConfig} loading={testingConfig}>
              测试配置
            </Button>
            <Button type="primary" onClick={handleConfigure} loading={configuring}>
              保存配置
            </Button>
          </Space>
        )
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" />
        </div>
      ) : (
        <>
          {status?.is_configured ? (
            <Alert
              message={
                <Space>
                  <CheckCircleOutlined />
                  <span>
                    当前使用: {status.active_provider_display}
                    {status.active_model ? ` / ${status.active_model}` : ''}
                    {status.supports_vision ? '（视觉）' : ''}
                  </span>
                </Space>
              }
              description="保存后用于月度字段提取、审核分类、跨文件核对和单张发票识别。下一份文档起生效，无需重建容器。"
              type="success"
              showIcon={false}
              style={{ marginBottom: 16 }}
            />
          ) : (
            <Alert
              message="未配置LLM服务"
              description="LLM 为可选增强功能，可提升发票识别准确率与比对能力。不配置也可使用 OCR-only 流程。"
              type="warning"
              showIcon
              icon={<ExclamationCircleOutlined />}
              style={{ marginBottom: 16 }}
            />
          )}

          {showVisionWarning && (
            <Alert
              message="当前模型不支持看图"
              description="月度资料的视觉升级和单张发票 Vision 会跳过，仅走文本识别或 OCR/规则兜底。"
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
            />
          )}

          <div style={{ marginBottom: 16 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>可用的LLM提供商:</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {status?.available_providers.map(renderProviderStatus)}
            </div>
          </div>

          <Divider />

          <Form
            form={form}
            layout="vertical"
          >
            <Form.Item
              name="provider"
              label="选择LLM提供商"
              rules={[{ required: true, message: '请选择LLM提供商' }]}
            >
              <Select
                placeholder="选择LLM提供商"
                onChange={handleProviderChange}
                options={status?.available_providers.map((item) => ({
                  label: item.display_name,
                  value: item.name,
                }))}
              />
            </Form.Item>

            <Form.Item
              name="api_key"
              label="API 密钥"
              rules={
                providerHasSavedKey
                  ? []
                  : [{ required: true, message: '请输入API密钥' }]
              }
            >
              <Input.Password
                placeholder={providerHasSavedKey ? '已保存，留空则保持原密钥' : '输入API密钥'}
                prefix={<ApiOutlined />}
              />
            </Form.Item>

            <Form.Item name="model" label="模型">
              <AutoComplete
                placeholder={modelsLoading ? '加载模型列表...' : '选择或输入模型名称'}
                allowClear
                options={models.map((item) => ({
                  label: (
                    <Space>
                      <span>{item.name || item.id}</span>
                      {item.vision ? (
                        <Tooltip title="支持图像识别">
                          <EyeOutlined style={{ color: '#1890ff' }} />
                        </Tooltip>
                      ) : (
                        <Tag>仅文本</Tag>
                      )}
                    </Space>
                  ),
                  value: item.id,
                }))}
                filterOption={(input, option) =>
                  (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>

            <Form.Item
              name="base_url"
              label="自定义 API 地址 (可选)"
              tooltip="用于自定义API端点或代理服务"
            >
              <Input placeholder="例如: https://api.example.com/v1" />
            </Form.Item>

            <Form.Item
              name="config_token"
              label="管理员令牌 (可选)"
              tooltip="如后端设置 LLM_CONFIG_TOKEN，请在此输入"
            >
              <Input.Password placeholder="输入管理员令牌" />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  );
}

export default LLMConfigModal;
