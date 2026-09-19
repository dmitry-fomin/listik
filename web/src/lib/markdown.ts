/**
 * markdown — единственная точка вывода пользовательского markdown в HTML на
 * доске. Своего разбора разметки здесь нет: заголовки, списки, ссылки,
 * инлайн-код и выделение делает рендер кита `renderMarkdownToHtml` (импорт по
 * подпути — при `moduleResolution: "bundler"` резолвится только он).
 *
 * Свой код — только то, чего рендер кита не понимает: блоки в тройных обратных
 * кавычках и таблицы GFM (`| Код | … |` со строкой-разделителем `|---|---|`).
 * Порядок обязателен: кит экранирует HTML до markdown-замен, поэтому готовый
 * `<pre><code>`/`<table>` до рендера был бы показан текстом. Такие блоки
 * вырезаются, вместо них подставляются плейсхолдеры, а уже после рендера
 * плейсхолдеры заменяются на собранный HTML.
 *
 * Содержимое ячеек таблицы прогоняется через тот же рендер кита (инлайн-код,
 * выделение, ссылки внутри ячейки работают, HTML экранируется), а обёртка `<p>`
 * с одиночной строки снимается — в `<td>` абзац не нужен.
 */
import { renderMarkdownToHtml } from '@zoloto585/facet/components/UiMarkdownEditor/markdown.ts'

/** Открывающая строка: ```` ``` ```` в начале строки с необязательным словом-языком. */
const FENCE_OPEN = /^```[ \t]*([^\s`]*)[ \t]*$/
/** Закрывающая строка: тройные обратные кавычки, пробелы по краям не мешают. */
const FENCE_CLOSE = /^```[ \t]*$/
/**
 * Строка-разделитель шапки таблицы: `|---|:--:|---:|`. Она и делает таблицу
 * таблицей — одной строки с трубами мало (обычный текст с `|` не должен
 * внезапно превращаться в таблицу).
 */
const TABLE_DELIMITER = /^\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?$/

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
 * Разбор строки таблицы на ячейки: внешние трубы необязательны, экранированная
 * труба (`\\|`) остаётся символом внутри ячейки.
 */
function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  const cells: string[] = []
  let current = ''
  for (let i = 0; i < trimmed.length; i += 1) {
    const char = trimmed[i]
    if (char === '\\' && trimmed[i + 1] === '|') {
      current += '|'
      i += 1
      continue
    }
    if (char === '|') {
      cells.push(current.trim())
      current = ''
      continue
    }
    current += char
  }
  cells.push(current.trim())
  return cells
}

/** Выравнивание колонок из строки-разделителя: `:---`, `:---:`, `---:`. */
function alignmentsOf(line: string): Array<'left' | 'center' | 'right' | null> {
  return splitRow(line).map((cell) => {
    const left = cell.startsWith(':')
    const right = cell.endsWith(':')
    if (left && right) return 'center'
    if (right) return 'right'
    if (left) return 'left'
    return null
  })
}

/** Содержимое ячейки: тот же рендер кита, но без обёртки `<p>`. */
function renderCell(source: string): string {
  const html = renderMarkdownToHtml(source).trim()
  const match = /^<p>([\s\S]*)<\/p>$/.exec(html)
  return match ? (match[1] ?? '') : html
}

/**
 * Сборка таблицы: шапка — из первой строки, число колонок задаёт она же (лишние
 * ячейки строки отбрасываются, недостающие добиваются пустыми, иначе кривая
 * строка в исходнике развалила бы вёрстку).
 */
function renderTable(headerLine: string, delimiterLine: string, bodyLines: string[]): string {
  const header = splitRow(headerLine)
  const aligns = alignmentsOf(delimiterLine)
  const width = header.length
  const cell = (tag: 'th' | 'td', text: string, index: number): string => {
    const align = aligns[index] ?? null
    const style = align ? ` style="text-align:${align}"` : ''
    return `<${tag}${style}>${renderCell(text)}</${tag}>`
  }
  const head = `<thead><tr>${header.map((text, index) => cell('th', text, index)).join('')}</tr></thead>`
  const rows = bodyLines.map((line) => {
    const cells = splitRow(line)
    const filled = Array.from({ length: width }, (_, index) => cells[index] ?? '')
    return `<tr>${filled.map((text, index) => cell('td', text, index)).join('')}</tr>`
  })
  const body = rows.length ? `<tbody>${rows.join('')}</tbody>` : ''
  return `<table class="listik-prose__table">${head}${body}</table>`
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
  /** Вырезанные блоки (код и таблицы) — готовый HTML под плейсхолдером. */
  const blocks: Array<{ placeholder: string; html: string }> = []
  const prepared: string[] = []
  let seq = 0

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    if (!FENCE_OPEN.test(line)) {
      // Таблица: строка с трубами, за которой идёт строка-разделитель.
      const next = lines[i + 1]
      if (line.includes('|') && next !== undefined && TABLE_DELIMITER.test(next.trim()) && next.includes('-')) {
        const bodyLines: string[] = []
        let j = i + 2
        while (j < lines.length && (lines[j] ?? '').includes('|') && (lines[j] ?? '').trim() !== '') {
          bodyLines.push(lines[j] ?? '')
          j += 1
        }
        const placeholder = placeholderFor(text, seq)
        seq += 1
        blocks.push({ placeholder, html: renderTable(line, next, bodyLines) })
        prepared.push('', placeholder, '')
        i = j - 1
        continue
      }
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
    blocks.push({ placeholder, html: `<pre><code>${escapeHtml(code.join('\n'))}</code></pre>` })
    // Пустые строки с обеих сторон: плейсхолдер обязан стать отдельным абзацем,
    // иначе кит склеит его `<br>` с соседним текстом и `<pre>` окажется в `<p>`.
    prepared.push('', placeholder, '')
  }

  let html = renderMarkdownToHtml(prepared.join('\n'))
  for (const block of blocks) {
    const rendered = block.html
    // Плейсхолдер на отдельной строке кит заворачивает в `<p>` — подменяем
    // вместе с обёрткой; одиночная замена — страховка от неожиданной склейки.
    html = html.split(`<p>${block.placeholder}</p>`).join(rendered)
    html = html.split(block.placeholder).join(rendered)
  }
  return html
}
