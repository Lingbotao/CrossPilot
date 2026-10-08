import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Select, Space, Table, Tag, Typography, Upload } from 'antd';
import { useState } from 'react';

import { productsApi } from '@/api/products';
import { Perm, type ProductImageType, type ProductImageView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.imagePage;

const TYPE_LABEL: Record<ProductImageType, string> = {
  MAIN: copy.typeMain,
  GALLERY: copy.typeGallery,
  APLUS: copy.typeAplus,
};

const ISSUE_LABEL: Record<string, string> = {
  TOO_SMALL: copy.issueTooSmall,
  TOO_LARGE: copy.issueTooLarge,
  NOT_WHITE: copy.issueNotWhite,
  BAD_TYPE: copy.issueBadType,
  TOO_HEAVY: copy.issueTooHeavy,
};

function issueText(code: string): string {
  return ISSUE_LABEL[code] ?? code;
}

export function ImagesPage() {
  const queryClient = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [spuId, setSpuId] = useState<string | undefined>();

  const products = useQuery({
    queryKey: ['spus', 'image-pick', keyword],
    queryFn: () => productsApi.list({ q: keyword, limit: 20 }),
    enabled: keyword.trim().length > 0,
  });
  const images = useQuery({
    queryKey: ['product-images', spuId],
    queryFn: () => productsApi.images(spuId ?? ''),
    enabled: Boolean(spuId),
  });

  const upload = useMutation({
    mutationFn: ({ imageType, file }: { imageType: ProductImageType; file: File }) =>
      productsApi.uploadImage(spuId ?? '', imageType, file),
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      await queryClient.invalidateQueries({ queryKey: ['product-images', spuId] });
    },
  });
  const remove = useMutation({
    mutationFn: (imageId: string) => productsApi.removeImage(imageId),
    onSuccess: async () => {
      feedback().message.success(copy.removed);
      await queryClient.invalidateQueries({ queryKey: ['product-images', spuId] });
    },
  });

  const uploadButton = (imageType: ProductImageType, label: string) => (
    <Upload
      accept="image/png,image/jpeg,image/webp,image/gif"
      showUploadList={false}
      beforeUpload={(file) => {
        upload.mutate({ imageType, file });
        return false;
      }}
    >
      <Button loading={upload.isPending}>{label}</Button>
    </Upload>
  );

  return (
    <Card title={copy.title}>
      <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          showSearch
          filterOption={false}
          placeholder={copy.searchProduct}
          style={{ width: 280 }}
          value={spuId}
          onSearch={setKeyword}
          onChange={setSpuId}
          options={(products.data?.items ?? []).map((item) => ({ value: item.id, label: item.title }))}
        />
        {spuId ? (
          <PermissionGuard permission={Perm.PRODUCT_WRITE}>
            <Space>
              {uploadButton('MAIN', copy.uploadMain)}
              {uploadButton('GALLERY', copy.uploadGallery)}
              {uploadButton('APLUS', copy.uploadAplus)}
            </Space>
          </PermissionGuard>
        ) : null}
      </Space>
      {spuId ? (
        <Table<ProductImageView>
          rowKey="id"
          loading={images.isLoading}
          dataSource={images.data ?? []}
          pagination={false}
          locale={{ emptyText: copy.emptyImages }}
          columns={[
            { title: copy.type, dataIndex: 'image_type', render: (value: ProductImageType) => TYPE_LABEL[value] },
            {
              title: copy.size,
              render: (_value, row) => `${row.width_px} × ${row.height_px}`,
            },
            {
              title: copy.white,
              dataIndex: 'white_background',
              render: (value: boolean) => (value ? copy.whiteYes : copy.whiteNo),
            },
            {
              title: copy.compliance,
              dataIndex: 'compliance',
              render: (_value, row) => (
                <Space size={[4, 4]} wrap>
                  {row.compliance.map((item) => (
                    <Tag key={item.platform_code} color={item.ok ? 'green' : 'orange'}>
                      {item.platform_code}
                      {item.ok ? ` ${copy.compliant}` : ` ${item.issues.map(issueText).join('、')}`}
                    </Tag>
                  ))}
                </Space>
              ),
            },
            {
              title: zhCN.common.actions,
              render: (_value, row) => (
                <PermissionGuard permission={Perm.PRODUCT_WRITE}>
                  <Button type="link" danger loading={remove.isPending} onClick={() => remove.mutate(row.id)}>
                    {copy.remove}
                  </Button>
                </PermissionGuard>
              ),
            },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">{copy.emptyProduct}</Typography.Text>
      )}
    </Card>
  );
}
