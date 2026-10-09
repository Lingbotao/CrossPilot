import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Input, Select, Space, Table, Tabs, Tag, Typography } from 'antd';
import { useState } from 'react';

import { listingsApi } from '@/api/listings';
import { localeApi } from '@/api/locale';
import {
  CONTENT_LANGUAGES,
  CONTENT_MARKETS,
  Perm,
  type ContentQuality,
  type GlossaryTermView,
  type ListingContentView,
  type SensitiveHit,
  type SensitiveTermView,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.localePage;

const LANG_LABEL: Record<string, string> = {
  'zh-CN': copy.langZhCN,
  'zh-TW': copy.langZhTW,
  en: copy.langEn,
  id: copy.langId,
  th: copy.langTh,
  vi: copy.langVi,
  ms: copy.langMs,
  es: copy.langEs,
  pt: copy.langPt,
  de: copy.langDe,
  fr: copy.langFr,
  it: copy.langIt,
  ja: copy.langJa,
};

const STATUS_LABEL: Record<ContentQuality, string> = {
  UNTRANSLATED: copy.statusUntranslated,
  MT_DRAFT: copy.statusDraft,
  REVIEWED: copy.statusReviewed,
  PUBLISHED: copy.statusPublished,
};

const STATUS_COLOR: Record<ContentQuality, string> = {
  UNTRANSLATED: 'default',
  MT_DRAFT: 'red',
  REVIEWED: 'blue',
  PUBLISHED: 'green',
};

const FIELD_LABEL: Record<string, string> = {
  title: copy.hitTitle,
  description: copy.hitDescription,
  bullet: copy.hitBullet,
};

function langLabel(code: string): string {
  return LANG_LABEL[code] ?? code;
}

function isMarket(code: string): boolean {
  return (CONTENT_MARKETS as readonly string[]).includes(code);
}

function bulletLines(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function languageOptions() {
  return CONTENT_LANGUAGES.map((code) => ({ value: code, label: langLabel(code) }));
}

export function LocalePage() {
  return (
    <Card title={copy.title}>
      <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
      <Tabs
        items={[
          { key: 'copy', label: copy.tabCopy, children: <CopyPanel /> },
          { key: 'glossary', label: copy.tabGlossary, children: <GlossaryPanel /> },
          { key: 'sensitive', label: copy.tabSensitive, children: <SensitivePanel /> },
        ]}
      />
    </Card>
  );
}

function CopyPanel() {
  const queryClient = useQueryClient();
  const [listingId, setListingId] = useState<string>();
  const [quality, setQuality] = useState<ContentQuality | undefined>();
  const [lang, setLang] = useState('en');
  const [sourceLang, setSourceLang] = useState('zh-CN');
  const [market, setMarket] = useState('SG');
  const [contentId, setContentId] = useState<string>();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [bullets, setBullets] = useState('');
  const [status, setStatus] = useState<ContentQuality | undefined>();
  const [replaced, setReplaced] = useState<ListingContentView['replaced_terms']>([]);
  const [hits, setHits] = useState<SensitiveHit[] | undefined>();

  const listings = useQuery({
    queryKey: ['listings', 'locale-pick'],
    queryFn: () => listingsApi.list({ limit: 50 }),
  });
  const contents = useQuery({
    queryKey: ['listing-contents', listingId ?? '', quality ?? ''],
    queryFn: () =>
      localeApi.listContents({
        listing_id: listingId,
        quality_status: quality,
        limit: 50,
      }),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['listing-contents'] });
  };

  const applyView = (view: ListingContentView) => {
    setContentId(view.id);
    setListingId(view.listing_id);
    setLang(view.lang);
    setTitle(view.title);
    setDescription(view.description);
    setBullets(view.bullet_points.join('\n'));
    setStatus(view.quality_status);
    setReplaced(view.replaced_terms);
    setHits(undefined);
  };

  const save = useMutation({
    mutationFn: (confirmReview: boolean) =>
      localeApi.saveContent({
        listing_id: listingId ?? '',
        lang,
        title: title.trim(),
        description: description.trim(),
        bullet_points: bulletLines(bullets),
        confirm_review: confirmReview,
      }),
    onSuccess: async (view, confirmReview) => {
      applyView(view);
      feedback().message.success(confirmReview ? copy.reviewed : copy.saved);
      await refresh();
    },
  });
  const draft = useMutation({
    mutationFn: () =>
      localeApi.machineDraft({
        listing_id: listingId ?? '',
        source_lang: sourceLang,
        target_lang: lang,
      }),
    onSuccess: async (view) => {
      applyView(view);
      feedback().message.success(copy.drafted);
      await refresh();
    },
  });
  const publish = useMutation({
    mutationFn: (id: string) => localeApi.publishContent(id),
    onSuccess: async (view) => {
      applyView(view);
      feedback().message.success(copy.published);
      await refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => localeApi.removeContent(id),
    onSuccess: async () => {
      setContentId(undefined);
      setTitle('');
      setDescription('');
      setBullets('');
      setStatus(undefined);
      setReplaced([]);
      feedback().message.success(copy.removed);
      await refresh();
    },
  });
  const scan = useMutation({
    mutationFn: () =>
      localeApi.scan({
        market,
        lang,
        title: title.trim(),
        description: description.trim(),
        bullet_points: bulletLines(bullets),
      }),
    onSuccess: (rows) => {
      setHits(rows);
      if (rows.length === 0) {
        feedback().message.info(copy.hitNone);
      }
    },
  });

  const listingRows = listings.data?.items ?? [];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <Select
          showSearch
          allowClear
          placeholder={copy.pickListing}
          style={{ minWidth: 280 }}
          optionFilterProp="label"
          value={listingId}
          options={listingRows.map((row) => ({
            value: row.id,
            label: `${row.sku_code} · ${row.shop_name} · ${row.site_code}`,
          }))}
          onChange={(value: string | undefined) => {
            setListingId(value);
            const row = listingRows.find((item) => item.id === value);
            if (row && isMarket(row.site_code)) {
              setMarket(row.site_code);
            }
            setContentId(undefined);
            setTitle('');
            setDescription('');
            setBullets('');
            setStatus(undefined);
            setReplaced([]);
            setHits(undefined);
          }}
        />
        <Select
          allowClear
          placeholder={copy.statusAll}
          style={{ minWidth: 160 }}
          value={quality}
          options={[
            { value: 'MT_DRAFT', label: copy.statusDraft },
            { value: 'UNTRANSLATED', label: copy.statusUntranslated },
            { value: 'REVIEWED', label: copy.statusReviewed },
            { value: 'PUBLISHED', label: copy.statusPublished },
          ]}
          onChange={(value: ContentQuality | undefined) => setQuality(value)}
        />
      </Space>
      <Table<ListingContentView>
        rowKey="id"
        loading={contents.isLoading}
        dataSource={contents.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: copy.emptyContents }}
        onRow={(row) => ({ onClick: () => applyView(row) })}
        columns={[
          { title: copy.lang, dataIndex: 'lang', render: (value: string) => langLabel(value) },
          { title: copy.titleField, dataIndex: 'title' },
          {
            title: copy.status,
            dataIndex: 'quality_status',
            render: (value: ContentQuality) => <Tag color={STATUS_COLOR[value]}>{STATUS_LABEL[value]}</Tag>,
          },
        ]}
      />
      {status === 'MT_DRAFT' ? <Alert type="warning" showIcon message={copy.draftBanner} /> : null}
      <Typography.Text type="secondary">{copy.publishHint}</Typography.Text>
      <Space wrap>
        <Typography.Text>{copy.lang}</Typography.Text>
        <Select
          style={{ minWidth: 160 }}
          value={lang}
          options={languageOptions()}
          onChange={(value) => {
            setLang(value);
            setContentId(undefined);
            setStatus(undefined);
            setReplaced([]);
          }}
        />
        <Typography.Text>{copy.sourceLang}</Typography.Text>
        <Select style={{ minWidth: 160 }} value={sourceLang} options={languageOptions()} onChange={setSourceLang} />
        <Typography.Text>{copy.market}</Typography.Text>
        <Select
          style={{ minWidth: 120 }}
          value={market}
          options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))}
          onChange={setMarket}
        />
      </Space>
      <Input placeholder={copy.titleField} value={title} maxLength={500} onChange={(event) => setTitle(event.target.value)} />
      <Input.TextArea
        placeholder={copy.descriptionField}
        value={description}
        rows={4}
        onChange={(event) => setDescription(event.target.value)}
      />
      <Input.TextArea
        placeholder={`${copy.bullets}（${copy.bulletsHint}）`}
        value={bullets}
        rows={4}
        onChange={(event) => setBullets(event.target.value)}
      />
      {replaced.length > 0 ? (
        <Space wrap>
          <Typography.Text>{copy.replaced}</Typography.Text>
          {replaced.map((item) => (
            <Tag key={`${item.source_term}-${item.target_term}`}>
              {item.source_term} → {item.target_term}
            </Tag>
          ))}
        </Space>
      ) : null}
      {hits && hits.length > 0 ? (
        <Space wrap>
          {hits.map((item) => (
            <Tag color="red" key={`${item.field}-${item.keyword}`}>
              {FIELD_LABEL[item.field] ?? item.field}「{item.keyword}」
              {item.suggest_replacement ? ` ${copy.hitSuggest}「${item.suggest_replacement}」` : ''}
            </Tag>
          ))}
        </Space>
      ) : null}
      <PermissionGuard permission={Perm.PRODUCT_WRITE}>
        <Space wrap>
          <Button disabled={!listingId} loading={save.isPending} onClick={() => save.mutate(false)}>
            {copy.save}
          </Button>
          <Button
            disabled={!listingId || status !== 'MT_DRAFT'}
            loading={save.isPending}
            onClick={() => save.mutate(true)}
          >
            {copy.confirmReview}
          </Button>
          <Button
            disabled={!listingId || sourceLang === lang}
            loading={draft.isPending}
            onClick={() => draft.mutate()}
          >
            {copy.machine}
          </Button>
          <Button
            type="primary"
            disabled={!contentId || status !== 'REVIEWED'}
            loading={publish.isPending}
            onClick={() => contentId && publish.mutate(contentId)}
          >
            {copy.publish}
          </Button>
          <Button
            danger
            disabled={!contentId}
            loading={remove.isPending}
            onClick={() => contentId && remove.mutate(contentId)}
          >
            {copy.remove}
          </Button>
        </Space>
      </PermissionGuard>
      <Button loading={scan.isPending} onClick={() => scan.mutate()}>
        {copy.scan}
      </Button>
    </Space>
  );
}

function GlossaryPanel() {
  const queryClient = useQueryClient();
  const [sourceLang, setSourceLang] = useState('zh-CN');
  const [targetLang, setTargetLang] = useState('en');
  const [sourceTerm, setSourceTerm] = useState('');
  const [targetTerm, setTargetTerm] = useState('');
  const terms = useQuery({
    queryKey: ['glossary-terms'],
    queryFn: () => localeApi.listGlossary({ limit: 50 }),
  });
  const create = useMutation({
    mutationFn: () =>
      localeApi.createGlossary({
        source_lang: sourceLang,
        source_term: sourceTerm.trim(),
        target_lang: targetLang,
        target_term: targetTerm.trim(),
      }),
    onSuccess: async () => {
      setSourceTerm('');
      setTargetTerm('');
      feedback().message.success(copy.glossarySaved);
      await queryClient.invalidateQueries({ queryKey: ['glossary-terms'] });
    },
  });
  const remove = useMutation({
    mutationFn: (termId: string) => localeApi.removeGlossary(termId),
    onSuccess: async () => {
      feedback().message.success(copy.termRemoved);
      await queryClient.invalidateQueries({ queryKey: ['glossary-terms'] });
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PermissionGuard permission={Perm.PRODUCT_WRITE}>
        <Space wrap>
          <Select style={{ minWidth: 140 }} value={sourceLang} options={languageOptions()} onChange={setSourceLang} />
          <Input
            placeholder={copy.sourceTerm}
            value={sourceTerm}
            maxLength={128}
            onChange={(event) => setSourceTerm(event.target.value)}
          />
          <Select style={{ minWidth: 140 }} value={targetLang} options={languageOptions()} onChange={setTargetLang} />
          <Input
            placeholder={copy.targetTerm}
            value={targetTerm}
            maxLength={128}
            onChange={(event) => setTargetTerm(event.target.value)}
          />
          <Button
            type="primary"
            disabled={!sourceTerm.trim() || !targetTerm.trim()}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {copy.addGlossary}
          </Button>
        </Space>
      </PermissionGuard>
      <Table<GlossaryTermView>
        rowKey="id"
        loading={terms.isLoading}
        dataSource={terms.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: copy.emptyGlossary }}
        columns={[
          { title: copy.sourceLang, dataIndex: 'source_lang', render: (value: string) => langLabel(value) },
          { title: copy.sourceTerm, dataIndex: 'source_term' },
          { title: copy.targetLang, dataIndex: 'target_lang', render: (value: string) => langLabel(value) },
          { title: copy.targetTerm, dataIndex: 'target_term' },
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
    </Space>
  );
}

function SensitivePanel() {
  const queryClient = useQueryClient();
  const [market, setMarket] = useState('SG');
  const [lang, setLang] = useState('en');
  const [keyword, setKeyword] = useState('');
  const [suggestion, setSuggestion] = useState('');
  const terms = useQuery({
    queryKey: ['sensitive-terms'],
    queryFn: () => localeApi.listSensitive({ limit: 50 }),
  });
  const create = useMutation({
    mutationFn: () =>
      localeApi.createSensitive({
        market,
        lang,
        keyword: keyword.trim(),
        suggest_replacement: suggestion.trim() || null,
      }),
    onSuccess: async () => {
      setKeyword('');
      setSuggestion('');
      feedback().message.success(copy.sensitiveSaved);
      await queryClient.invalidateQueries({ queryKey: ['sensitive-terms'] });
    },
  });
  const remove = useMutation({
    mutationFn: (termId: string) => localeApi.removeSensitive(termId),
    onSuccess: async () => {
      feedback().message.success(copy.termRemoved);
      await queryClient.invalidateQueries({ queryKey: ['sensitive-terms'] });
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PermissionGuard permission={Perm.PRODUCT_WRITE}>
        <Space wrap>
          <Select
            style={{ minWidth: 120 }}
            value={market}
            options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))}
            onChange={setMarket}
          />
          <Select style={{ minWidth: 140 }} value={lang} options={languageOptions()} onChange={setLang} />
          <Input
            placeholder={copy.keyword}
            value={keyword}
            maxLength={128}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Input
            placeholder={copy.suggestion}
            value={suggestion}
            maxLength={128}
            onChange={(event) => setSuggestion(event.target.value)}
          />
          <Button type="primary" disabled={!keyword.trim()} loading={create.isPending} onClick={() => create.mutate()}>
            {copy.addSensitive}
          </Button>
        </Space>
      </PermissionGuard>
      <Table<SensitiveTermView>
        rowKey="id"
        loading={terms.isLoading}
        dataSource={terms.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: copy.emptySensitive }}
        columns={[
          { title: copy.market, dataIndex: 'market' },
          { title: copy.lang, dataIndex: 'lang', render: (value: string) => langLabel(value) },
          { title: copy.keyword, dataIndex: 'keyword' },
          { title: copy.suggestion, dataIndex: 'suggest_replacement' },
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
    </Space>
  );
}
