import { test, expect } from '@playwright/test'

const DOCX_FILE = {
  name: 'test.docx',
  mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  buffer: Buffer.from('PK'),
}

/** Intercept the upload so it hangs — keeps the loading UI visible for assertions. */
async function interceptUpload(page: Parameters<typeof test>[1] extends (...args: infer A) => unknown ? A[0] : never) {
  await page.route('**/api/check', () => {
    /* never respond — upload stays pending */
  })
}

test.describe('Loading page', () => {
  test('redirects to / when accessed directly without file', async ({ page }) => {
    await page.goto('/loading')
    await expect(page).toHaveURL('/')
  })

  test('shows loading heading and filename', async ({ page }) => {
    await interceptUpload(page)
    await page.goto('/')
    await page.locator('input[type="file"]').setInputFiles(DOCX_FILE)
    await expect(page).toHaveURL(/\/loading/)
    await expect(page.getByText('Checking your citations')).toBeVisible()
    await expect(page.getByText('test.docx')).toBeVisible()
    await expect(page.getByText('~5 seconds')).toBeVisible()
  })

  test('shows all 5 steps on loading page', async ({ page }) => {
    await interceptUpload(page)
    await page.goto('/')
    await page.locator('input[type="file"]').setInputFiles(DOCX_FILE)
    await expect(page).toHaveURL(/\/loading/)
    await Promise.all([
      expect(page.getByText('Parsing document')).toBeVisible(),
      expect(page.getByText('Extracting citations')).toBeVisible(),
      expect(page.getByText('Verifying with Crossref')).toBeVisible(),
      expect(page.getByText('Running APA validator')).toBeVisible(),
      expect(page.getByText('Building report')).toBeVisible(),
    ])
  })

  test('shows error state with back button when upload fails', async ({ page }) => {
    // No route intercept — backend not running → upload fails → error UI shown
    await page.goto('/')
    await page.locator('input[type="file"]').setInputFiles(DOCX_FILE)
    await expect(page).toHaveURL(/\/loading/)
    await expect(page.getByText('Back to upload')).toBeVisible({ timeout: 10_000 })
    await page.getByText('Back to upload').click()
    await expect(page).toHaveURL('/')
  })
})
