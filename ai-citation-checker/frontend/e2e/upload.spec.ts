import { test, expect } from '@playwright/test'

test.describe('Upload page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('renders navbar with app name and APA badge', async ({ page }) => {
    await expect(page.getByText('Citation Checker')).toBeVisible()
    await expect(page.getByText('APA 7th', { exact: true })).toBeVisible()
  })

  test('renders hero heading', async ({ page }) => {
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Verify every citation')
  })

  test('renders drop zone', async ({ page }) => {
    await expect(page.getByText('Drag & drop your essay')).toBeVisible()
    await expect(page.getByText('click to browse')).toBeVisible()
  })

  test('renders all four feature pills', async ({ page }) => {
    await expect(page.getByText('🔴 Fabricated references')).toBeVisible()
    await expect(page.getByText('🟡 Metadata mismatches')).toBeVisible()
    await expect(page.getByText('🟡 APA format violations')).toBeVisible()
    await expect(page.getByText('Verified citations')).toBeVisible()
  })

  test('renders privacy note', async ({ page }) => {
    await expect(page.getByText('No login required')).toBeVisible()
  })

  test('language toggle button shows 中文 (default is English)', async ({ page }) => {
    await expect(page.getByRole('button', { name: '中文' })).toBeVisible()
  })

  test('language toggle switches to Chinese', async ({ page }) => {
    await page.getByRole('button', { name: '中文' }).click()
    await expect(page.getByText('引文检查器')).toBeVisible()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('提交前')
    await expect(page.getByText('拖拽上传论文')).toBeVisible()
  })

  test('language toggle switches back to English', async ({ page }) => {
    await page.getByRole('button', { name: '中文' }).click()
    await page.getByRole('button', { name: 'EN' }).click()
    await expect(page.getByText('Citation Checker')).toBeVisible()
    await expect(page.getByText('Drag & drop your essay')).toBeVisible()
  })

  test('shows error for unsupported file type', async ({ page }) => {
    const input = page.locator('input[type="file"]')
    await input.setInputFiles({
      name: 'essay.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('hello'),
    })
    await expect(page.getByText('Only .docx and .pdf files are supported')).toBeVisible()
  })

  test('navigates to /loading when valid docx is selected', async ({ page }) => {
    const input = page.locator('input[type="file"]')
    await input.setInputFiles({
      name: 'essay.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: Buffer.from('PK'),
    })
    await expect(page).toHaveURL(/\/loading/)
  })
})
