/**
 * markdown — единственная точка вывода пользовательского markdown в HTML на
 * доске. Своего разбора разметки здесь нет: заголовки, списки, ссылки,
 * инлайн-код и выделение делает рендер кита `renderMarkdownToHtml` (импорт по
 * подпути — при `moduleResolution: "bundler"` резолвится только он).
 *
 * Свой код — только блоки в тройных обратных кавычках, которых рендер кита не
 * понимает. Порядок обязателен: кит экранирует HTML до markdown-замен, поэтому
 * готовый `<pre><code>` до рендера был бы показан текстом. Блоки вырезаются,
 * вместо них подставляются плейсхолдеры, а уже после рендера плейсхолдеры
 * заменяются на экранированный `<pre><code>`.
 */
import { renderMarkdownToHtml } from '@zoloto585/facet/components/UiMarkdownEditor/markdown.ts'

/** Открывающая строка: ```` ``` ```` в начале строки с необязательным словом-языком. */
const FENCE_OPEN = /^```[ \t]*([^\s`]*)[ \t]*$/
/** Закрывающая строка: тройные обратные кавычки, пробелы по краям не мешают. */
const FENCE_CLOSE = /^```[ \t]*$/

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * Плейсхолдер — только латиница и цифры: рендер кита его не тронет (ни
 * экранирование, ни заголовки/списки/инлайн-разметка). Если такая строка уже
 * есть в исходном тексте, она удлиняется, чтобы подмена не задела чужой текст.
 */
function placeholderFor(source: string, seq: number): string {
  let candidate = `LKMDPHX${seq}`
  while (source.includes(candidate)) candidate += 'X'
  return candidate
}

/**
 * Рендер markdown в безопасный HTML. Пустое значение (в том числе `null` /
 * `undefined`) — пустая строка. Незакрытый блок кода считается закрытым концом
 * текста и рендерится как блок; исключений не бросаем.
 */
export function renderMarkdown(source: string | null | undefined): string {
  if (source == null || source === '') return ''
  const text = String(source)
  const lines = text.split(/\r?\n/)
  const codeBlocks: Array<{ placeholder: string; code: string }> = []
  const prepared: string[] = []
  let seq = 0

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    if (!FENCE_OPEN.test(line)) {
      prepared.push(line)
      continue
    }

    const code: string[] = []
    i += 1
    while (i < lines.length && !FENCE_CLOSE.test(lines[i] ?? '')) {
      code.push(lines[i] ?? '')
      i += 1
    }

    const placeholder = placeholderFor(text, seq)
    seq += 1
    codeBlocks.push({ placeholder, code: code.join('\n') })
    // Пустые строки с обеих сторон: плейсхолдер обязан стать отдельным абзацем,
    // иначе кит склеит его `<br>` с соседним текстом и `<pre>` окажется в `<p>`.
    prepared.push('', placeholder, '')
  }

  let html = renderMarkdownToHtml(prepared.join('\n'))
  for (const block of codeBlocks) {
    const rendered = `<pre><code>${escapeHtml(block.code)}</code></pre>`
    // Плейсхолдер на отдельной строке кит заворачивает в `<p>` — подменяем
    // вместе с обёрткой; одиночная замена — страховка от неожиданной склейки.
    html = html.split(`<p>${block.placeholder}</p>`).join(rendered)
    html = html.split(block.placeholder).join(rendered)
  }
  return html
}
