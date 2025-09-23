import React, { useState, useCallback } from 'react';
import {
  Card,
  Input,
  Button,
  Upload,
  Table,
  Form,
  Space,
  message,
  Progress,
  Tag,
  Tooltip,
  Row,
  Col,
  Typography,
  Alert,
  Divider,
  Modal,
  Spin
} from 'antd';
import {
  UploadOutlined,
  TableOutlined,
  FunctionOutlined,
  SendOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileImageOutlined,
  WarningOutlined
} from '@ant-design/icons';
import { UploadFile, RcFile } from 'antd/lib/upload';
import 'katex/dist/katex.min.css';
import { InlineMath, BlockMath } from 'react-katex';

const { TextArea } = Input;
const { Title, Text, Paragraph } = Typography;

interface MultimodalItem {
  id: string;
  type: 'image' | 'table' | 'equation';
  content?: string;
  preview?: string;
  description?: string;
  file?: File;
  status: 'pending' | 'processing' | 'completed' | 'error';
  error?: string;
}

interface QueryResult {
  result: string;
  citations: Array<{
    type: string;
    index: number;
    content: string;
    relevance: string;
  }>;
  processing_stats: {
    processing_time: number;
    items_processed: number;
    cache_hits: number;
  };
}

const MultimodalQuery: React.FC = () => {
  const [query, setQuery] = useState('');
  const [multimodalItems, setMultimodalItems] = useState<MultimodalItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [previewModal, setPreviewModal] = useState<{
    visible: boolean;
    item: MultimodalItem | null;
  }>({ visible: false, item: null });

  // Image upload handling with validation
  const beforeImageUpload = (file: RcFile): boolean | Promise<boolean> => {
    const isImage = file.type.startsWith('image/');
    if (!isImage) {
      message.error('You can only upload image files!');
      return false;
    }

    const isLt10M = file.size / 1024 / 1024 < 10;
    if (!isLt10M) {
      message.error('Image must be smaller than 10MB!');
      return false;
    }

    // Check image dimensions
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const img = new Image();
        img.onload = () => {
          if (img.width > 4096 || img.height > 4096) {
            message.warning('Image will be resized to fit 4096x4096 maximum dimensions');
          }
          resolve(true);
        };
        img.src = e.target?.result as string;
      };
      reader.readAsDataURL(file);
    });
  };

  const handleImageUpload = useCallback(async (file: RcFile) => {
    const reader = new FileReader();
    reader.onload = async (e) => {
      const base64 = e.target?.result?.toString().split(',')[1];
      const previewUrl = e.target?.result?.toString();

      const newItem: MultimodalItem = {
        id: `img_${Date.now()}`,
        type: 'image',
        content: base64,
        preview: previewUrl,
        description: `Image: ${file.name}`,
        file: file,
        status: 'pending'
      };

      setMultimodalItems(prev => [...prev, newItem]);
      message.success(`Image ${file.name} added successfully`);
    };
    reader.readAsDataURL(file);
  }, []);

  // Table input handling
  const handleTableInput = useCallback(() => {
    Modal.confirm({
      title: 'Add Table Data',
      content: (
        <div>
          <TextArea
            rows={6}
            placeholder='Enter CSV data or JSON format:
Example CSV:
Name,Age,Score
John,25,85
Jane,30,92

Example JSON:
{"headers": ["Name", "Age"], "rows": [["John", 25], ["Jane", 30]]}'
          />
        </div>
      ),
      onOk: (close) => {
        const textarea = document.querySelector('.ant-modal-body textarea') as HTMLTextAreaElement;
        const value = textarea?.value;

        if (value) {
          try {
            // Try parsing as JSON first
            const tableData = JSON.parse(value);
            const newItem: MultimodalItem = {
              id: `table_${Date.now()}`,
              type: 'table',
              content: JSON.stringify(tableData),
              description: 'User provided table',
              status: 'pending'
            };
            setMultimodalItems(prev => [...prev, newItem]);
            message.success('Table added successfully');
          } catch {
            // Try parsing as CSV
            const lines = value.trim().split('\n');
            if (lines.length > 1) {
              const headers = lines[0].split(',').map(h => h.trim());
              const rows = lines.slice(1).map(line =>
                line.split(',').map(cell => cell.trim())
              );
              const tableData = { headers, rows };
              const newItem: MultimodalItem = {
                id: `table_${Date.now()}`,
                type: 'table',
                content: JSON.stringify(tableData),
                description: 'CSV table',
                status: 'pending'
              };
              setMultimodalItems(prev => [...prev, newItem]);
              message.success('Table added successfully');
            } else {
              message.error('Invalid table format');
            }
          }
        }
      }
    });
  }, []);

  // Equation input handling
  const handleEquationInput = useCallback(() => {
    Modal.confirm({
      title: 'Add Mathematical Equation',
      content: (
        <div>
          <TextArea
            rows={3}
            placeholder="Enter LaTeX equation:
Example: E = mc^2
or: \frac{d}{dx} \int_a^x f(t) dt = f(x)"
          />
          <div style={{ marginTop: 10 }}>
            <Text type="secondary">Preview will appear here</Text>
          </div>
        </div>
      ),
      onOk: () => {
        const textarea = document.querySelector('.ant-modal-body textarea') as HTMLTextAreaElement;
        const latex = textarea?.value;

        if (latex) {
          const newItem: MultimodalItem = {
            id: `eq_${Date.now()}`,
            type: 'equation',
            content: latex,
            description: 'Mathematical equation',
            status: 'pending'
          };
          setMultimodalItems(prev => [...prev, newItem]);
          message.success('Equation added successfully');
        }
      }
    });
  }, []);

  // Execute multimodal query
  const executeQuery = useCallback(async () => {
    if (!query.trim()) {
      message.error('Please enter a query');
      return;
    }

    if (multimodalItems.length === 0) {
      message.warning('No multimodal content added. Executing text-only query.');
    }

    setLoading(true);
    setResult(null);

    try {
      // Update item statuses
      setMultimodalItems(prev =>
        prev.map(item => ({ ...item, status: 'processing' as const }))
      );

      // Prepare request
      const requestBody = {
        query: query,
        multimodal_content: multimodalItems.map(item => ({
          type: item.type,
          content: item.content,
          description: item.description
        })),
        mode: 'hybrid',
        vlm_enhanced: true
      };

      const response = await fetch('/api/v1/query/multimodal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });

      const data = await response.json();

      if (data.success) {
        setResult(data);
        setMultimodalItems(prev =>
          prev.map(item => ({ ...item, status: 'completed' as const }))
        );
        message.success('Query completed successfully');
      } else {
        throw new Error(data.error || 'Query failed');
      }
    } catch (error: any) {
      message.error(`Query failed: ${error.message}`);
      setMultimodalItems(prev =>
        prev.map(item => ({ ...item, status: 'error' as const, error: error.message }))
      );
    } finally {
      setLoading(false);
    }
  }, [query, multimodalItems]);

  // Remove item
  const removeItem = useCallback((id: string) => {
    setMultimodalItems(prev => prev.filter(item => item.id !== id));
  }, []);

  // Preview item
  const previewItem = useCallback((item: MultimodalItem) => {
    setPreviewModal({ visible: true, item });
  }, []);

  // Render item preview
  const renderItemPreview = (item: MultimodalItem) => {
    switch (item.type) {
      case 'image':
        return (
          <div style={{ textAlign: 'center' }}>
            {item.preview && (
              <img
                src={item.preview}
                alt="Preview"
                style={{ maxWidth: '100%', maxHeight: 200 }}
              />
            )}
          </div>
        );

      case 'table':
        if (item.content) {
          try {
            const tableData = JSON.parse(item.content);
            return (
              <Table
                size="small"
                dataSource={tableData.rows.slice(0, 3).map((row: any[], index: number) => ({
                  key: index,
                  ...row.reduce((acc: any, cell: any, i: number) => ({
                    ...acc,
                    [tableData.headers[i]]: cell
                  }), {})
                }))}
                columns={tableData.headers.map((header: string) => ({
                  title: header,
                  dataIndex: header,
                  key: header,
                  ellipsis: true
                }))}
                pagination={false}
              />
            );
          } catch {
            return <Text type="secondary">Invalid table data</Text>;
          }
        }
        return null;

      case 'equation':
        return (
          <div style={{ textAlign: 'center', padding: 10 }}>
            {item.content && (
              <BlockMath math={item.content} />
            )}
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <Card title="Multimodal Query Interface" style={{ margin: 24 }}>
      {/* Query Input Section */}
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Title level={4}>Your Question</Title>
          <TextArea
            rows={4}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Enter your question about the uploaded content. For example: 'What patterns can you identify in this data?' or 'Explain the relationship between the image and the equation.'"
            disabled={loading}
          />
        </Col>
      </Row>

      <Divider />

      {/* Multimodal Content Section */}
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Title level={4}>Multimodal Content</Title>
          <Space>
            <Upload
              beforeUpload={beforeImageUpload}
              customRequest={({ file }) => {
                handleImageUpload(file as RcFile);
              }}
              showUploadList={false}
              accept="image/*"
              disabled={loading}
            >
              <Button icon={<FileImageOutlined />} disabled={loading}>
                Add Image
              </Button>
            </Upload>

            <Button
              icon={<TableOutlined />}
              onClick={handleTableInput}
              disabled={loading}
            >
              Add Table
            </Button>

            <Button
              icon={<FunctionOutlined />}
              onClick={handleEquationInput}
              disabled={loading}
            >
              Add Equation
            </Button>
          </Space>
        </Col>

        {/* Items List */}
        <Col span={24}>
          {multimodalItems.length > 0 && (
            <Space direction="vertical" style={{ width: '100%' }}>
              {multimodalItems.map(item => (
                <Card
                  key={item.id}
                  size="small"
                  title={
                    <Space>
                      {item.type === 'image' && <FileImageOutlined />}
                      {item.type === 'table' && <TableOutlined />}
                      {item.type === 'equation' && <FunctionOutlined />}
                      <Text>{item.description}</Text>
                      <Tag color={
                        item.status === 'completed' ? 'success' :
                        item.status === 'processing' ? 'processing' :
                        item.status === 'error' ? 'error' : 'default'
                      }>
                        {item.status}
                      </Tag>
                    </Space>
                  }
                  extra={
                    <Space>
                      <Tooltip title="Preview">
                        <Button
                          size="small"
                          icon={<EyeOutlined />}
                          onClick={() => previewItem(item)}
                        />
                      </Tooltip>
                      <Tooltip title="Remove">
                        <Button
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => removeItem(item.id)}
                          disabled={loading}
                        />
                      </Tooltip>
                    </Space>
                  }
                >
                  {renderItemPreview(item)}
                  {item.error && (
                    <Alert
                      type="error"
                      message={item.error}
                      showIcon
                      style={{ marginTop: 8 }}
                    />
                  )}
                </Card>
              ))}
            </Space>
          )}

          {multimodalItems.length === 0 && (
            <Alert
              message="No multimodal content added"
              description="Add images, tables, or equations to enhance your query"
              type="info"
              showIcon
            />
          )}
        </Col>
      </Row>

      <Divider />

      {/* Execute Button */}
      <Row>
        <Col span={24}>
          <Button
            type="primary"
            size="large"
            icon={<SendOutlined />}
            onClick={executeQuery}
            loading={loading}
            disabled={!query.trim()}
            style={{ width: '100%' }}
          >
            Execute Multimodal Query
          </Button>
        </Col>
      </Row>

      {/* Results Section */}
      {result && (
        <>
          <Divider />
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <Title level={4}>Query Result</Title>
              <Card>
                <Paragraph>{result.result}</Paragraph>

                {result.citations.length > 0 && (
                  <>
                    <Divider />
                    <Title level={5}>Citations</Title>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      {result.citations.map((citation, index) => (
                        <Alert
                          key={index}
                          message={`${citation.type} (Item ${citation.index + 1})`}
                          description={citation.content}
                          type={citation.relevance === 'high' ? 'success' : 'info'}
                        />
                      ))}
                    </Space>
                  </>
                )}

                <Divider />
                <Title level={5}>Processing Statistics</Title>
                <Row gutter={16}>
                  <Col span={8}>
                    <Text strong>Processing Time:</Text>
                    <br />
                    <Text>{result.processing_stats.processing_time.toFixed(2)}s</Text>
                  </Col>
                  <Col span={8}>
                    <Text strong>Items Processed:</Text>
                    <br />
                    <Text>{result.processing_stats.items_processed}</Text>
                  </Col>
                  <Col span={8}>
                    <Text strong>Cache Hits:</Text>
                    <br />
                    <Text>{result.processing_stats.cache_hits}</Text>
                  </Col>
                </Row>
              </Card>
            </Col>
          </Row>
        </>
      )}

      {/* Preview Modal */}
      <Modal
        visible={previewModal.visible}
        title={previewModal.item?.description}
        footer={null}
        onCancel={() => setPreviewModal({ visible: false, item: null })}
        width={800}
      >
        {previewModal.item && (
          <div style={{ maxHeight: '60vh', overflow: 'auto' }}>
            {renderItemPreview(previewModal.item)}
          </div>
        )}
      </Modal>
    </Card>
  );
};

export default MultimodalQuery;