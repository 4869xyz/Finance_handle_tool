import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, Space, Spin, Typography, message } from 'antd';
import dayjs from 'dayjs';

import { getCompanyProfile, updateCompanyProfile } from '../services/api';
import type { CompanyProfileUpdate } from '../types/settings';

const { Paragraph, Text, Title } = Typography;
const emptyIdentity = { company_name: '', tax_id: '' };

export default function CompanyProfilePage() {
  const [form] = Form.useForm<CompanyProfileUpdate>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [version, setVersion] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    getCompanyProfile()
      .then((profile) => {
        if (profile) {
          form.setFieldsValue({ identities: profile.identities });
          setVersion(profile.version);
          setUpdatedAt(profile.updated_at);
        } else {
          form.setFieldsValue({ identities: [emptyIdentity] });
        }
      })
      .catch(() => message.error('读取公司档案失败'))
      .finally(() => setLoading(false));
  }, [form]);

  const handleSave = async (values: CompanyProfileUpdate) => {
    setSaving(true);
    try {
      const saved = await updateCompanyProfile(values);
      setVersion(saved.version);
      setUpdatedAt(saved.updated_at);
      form.setFieldsValue({ identities: saved.identities });
      message.success(`公司档案版本 ${saved.version} 已保存`);
    } catch {
      message.error('保存公司档案失败，请检查输入后重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <Space direction="vertical" size={4} style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0 }}>公司档案</Title>
        <Text type="secondary">配置可接受的发票购买方抬头及其纳税人识别号，保存后自动创建新版本。</Text>
      </Space>

      <Card style={{ maxWidth: 760 }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: 48 }}><Spin size="large" /></div>
        ) : (
          <>
            {version ? (
              <Alert
                type="success"
                showIcon
                message={`当前版本：${version}`}
                description={updatedAt ? `更新时间：${dayjs(updatedAt).format('YYYY-MM-DD HH:mm')}` : undefined}
                style={{ marginBottom: 20 }}
              />
            ) : (
              <Alert
                type="warning"
                showIcon
                message="尚未配置公司档案"
                description="标准发票将标记为待复核，直到保存至少一个公司主体。"
                style={{ marginBottom: 20 }}
              />
            )}

            <Form form={form} layout="vertical" onFinish={handleSave} initialValues={{ identities: [emptyIdentity] }}>
              <Form.List
                name="identities"
                rules={[{
                  validator: async (_, identities) => {
                    if (!identities?.length) {
                      return Promise.reject(new Error('请至少添加一个公司主体'));
                    }
                  },
                }]}
              >
                {(fields, { add, remove }, { errors }) => (
                  <>
                    {fields.map((field, index) => (
                      <Card
                        key={field.key}
                        size="small"
                        title={`公司主体 ${index + 1}`}
                        extra={fields.length > 1 ? (
                          <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(field.name)}>
                            删除
                          </Button>
                        ) : null}
                        style={{ marginBottom: 12 }}
                      >
                        <Form.Item
                          {...field}
                          name={[field.name, 'company_name']}
                          label="公司抬头"
                          rules={[{ required: true, whitespace: true, message: '请输入公司抬头' }]}
                        >
                          <Input maxLength={255} placeholder="请输入发票购买方公司抬头" />
                        </Form.Item>
                        <Form.Item
                          {...field}
                          name={[field.name, 'tax_id']}
                          label="纳税人识别号"
                          rules={[{ required: true, whitespace: true, message: '请输入纳税人识别号' }]}
                          style={{ marginBottom: 0 }}
                        >
                          <Input maxLength={50} placeholder="请输入该公司对应的纳税人识别号" />
                        </Form.Item>
                      </Card>
                    ))}
                    <Form.ErrorList errors={errors} />
                    <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add(emptyIdentity)}>
                      添加公司主体
                    </Button>
                  </>
                )}
              </Form.List>

              <Paragraph type="secondary" style={{ marginTop: 16 }}>
                系统会规范化全半角、空格和常见标点后进行精确匹配。只有以上配置的购买方抬头可以通过核验，其他、空白或无法识别的抬头会被排除。
              </Paragraph>
              <Button type="primary" htmlType="submit" loading={saving}>保存新版本</Button>
            </Form>
          </>
        )}
      </Card>
    </div>
  );
}
