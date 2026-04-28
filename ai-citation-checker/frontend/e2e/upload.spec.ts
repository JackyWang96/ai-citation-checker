import { test, expect } from '@playwright/test'

test.describe('Upload page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('renders navbar with app name and APA badge', async ({ page }) => {
    await expect(page.getByText('引文检查器')).toBeVisible()
    await expect(page.getByText('APA 7th')).toBeVisible()
  })

  test('renders hero heading', async ({ page }) => {
    await expect(page.getByRole('heading', { level: 1 })).toContainText('提交前，验证每一条引用')
  })

  test('renders drop zone', async ({ page }) => {
    await expect(page.getByText('拖拽上传论文')).toBeVisible()
    await expect(page.getByText('点击选择文件')).toBeVisible()
  })

  test('renders all four feature pills', async ({ page }) => {
    await expect(page.getByText('虚假引用检测')).toBeVisible()
    await expect(page.getByText('元数据不一致')).toBeVisible()
    // Use the pill div specifically (text also appears in hero body copy)
    await expect(page.getByText('🟡 APA 格式违规')).toBeVisible()
    await expect(page.getByText('引用验证通过')).toBeVisible()
  })

  test('renders privacy note', async ({ page }) => {
    await expect(page.getByText('无需登录')).toBeVisible()
  })

  test('language toggle switches to English', async ({ page }) => {
    const toggle = page.getByRole('button', { name: 'EN' })
    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(page.getByText('Citation Checker')).toBeVisible()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Verify every citation')
    await expect(page.getByText('Drag & drop your essay')).toBeVisible()
  })

  test('language toggle switches back to Chinese', async ({ page }) => {
    await page.getByRole('button', { name: 'EN' }).click()
    await page.getByRole('button', { name: '中文' }).click()
    await expect(page.getByText('引文检查器')).toBeVisible()
    await expect(page.getByText('拖拽上传论文')).toBeVisible()
  })

  test('shows error for unsupported file type', async ({ page }) => {
    const input = page.locator('input[type="file"]')
    await input.setInputFiles({
      name: 'essay.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('hello'),
    })
    await expect(page.getByText('仅支持 .docx 和 .pdf 文件')).toBeVisible()
  })

  test('navigates to /loading when valid docx is selected', async ({ page }) => {
    const input = page.locator('input[type="file"]')
    await input.setInputFiles({
      name: 'essay.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: Buffer.from('PK'), // minimal docx-like bytes
    })
    await expect(page).toHaveURL(/\/loading/)
  })
})
