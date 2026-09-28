import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {QUESTION_REASONS, questionCode} from "../log.mjs";
import {questionReason} from "../main.mjs";
import {textSlicedStuck} from "../decide.mjs";
import {limitText} from "../rollback.mjs";

const ROOT = new URL("../../", import.meta.url);
const source = (rel) => fs.readFileSync(new URL(rel, ROOT), "utf8");

// Код → полный текст вопроса, как его пишет место постановки.
const SAMPLES = {
  unscoped: "рой: у задачи нет write_scope — какие файлы и каталоги она правит? Ответь listik set a …",
  sliced_stuck: textSlicedStuck("a"),
  crashed: "рой: процесс задачи завершился (код 1, поколение 2), а карточка не закрыта. Лог: /x",
  no_ports: "рой: задача зависла, но перезапустить нечем — свободных портов в диапазоне нет; лог /x",
  hung: "рой: задача зависла (бежит дольше 60 мин) после 2 перезапусков — процесс снят, больше не трогаю",
  budget: "рой: бюджет прогона исчерпан — перезапуск не делаю (зависла). Разбери лог /x",
  process_not_stopped: "рой: полномочия отозваны, но процесс 123 не снят — сними его сам (kill)",
  worktree_failed: "рой: не удалось завести рабочее дерево: fatal: branch exists",
  freeze_limit: limitText("a", {count: 3, window: [], drift: {}, minutes: 5, total: 3, minutesTotal: 9}, 3),
  rejected: "рой: не влита — отклонена 2 раз: тесты красные после исправления: listik needs-owner a --clear",
  unmerged: "рой: не влита — конфликт слияния в a.py после исправления: listik needs-owner a --clear",
  halt: "рой: интеграционные тесты красные после слияния a, b.\nлог: …",
  no_role_after: "рой: после s3-impl роли нет, карточку не закрываю. Закрой сам",
  portions_no_role: "рой: порции заведены, но после s1-spec роли нет",
  portions_cancelled: "рой: все порции отменены, родитель не закрыт",
  route_no_roles: "рой: у маршрута low-pipeline нет роли с командой — маршрут не выбираю",
  no_criteria: "рой: нет критериев роли impl (этап s3-impl): файл не найден",
  no_workdir: "рой: нет рабочего каталога (worktree или path проекта listik)",
  claim_failed: "рой: не взял карточку за dsh (этап s3-impl, роль impl): держит другой",
  start_failed: "рой: этап s3-impl (impl) не запустился: No such file",
  not_delivered: "рой: этап s3-impl (impl) не сдал работу (последняя строка: не смог). Лог: /x",
  empty_question: "рой: этап s2-review (critic) ответил «вопрос» без текста. Лог: /x",
  outcome_error: "рой: не разобрал исход этапа: boom",
  autostart_refused: "автостарт не выполнен: маршрута x нет в базе — нужен ты",
};

// Коды, тексты которых ставит Python: файл и начало текста как в исходнике.
const PYTHON = {
  no_role_after: [["listik/launcher.py", "рой: после {stage} роли нет"],
    ["listik/stage_launch.py", "рой: после {stage} роли нет"]],
  portions_no_role: [["listik/stage_launch.py", "рой: порции заведены, но после s1-spec роли нет"]],
  portions_cancelled: [["listik/launcher.py", "рой: все порции отменены"]],
  route_no_roles: [["listik/launcher.py", "рой: у маршрута {key} нет роли с командой"]],
  no_criteria: [["listik/launcher.py", "рой: нет критериев роли {role}"]],
  no_workdir: [["listik/launcher.py", "рой: нет рабочего каталога"]],
  claim_failed: [["listik/launcher.py", "рой: не взял карточку за {harness}"]],
  start_failed: [["listik/launcher.py", "рой: этап {stage} ({role}) не запустился:"]],
  not_delivered: [["listik/stage_launch.py", "рой: этап {stage} ({role}) не сдал работу"]],
  empty_question: [["listik/stage_launch.py", "рой: этап {stage} ({role}) ответил «вопрос» без текста"]],
  outcome_error: [["listik/stage_launch.py", "рой: не разобрал исход этапа"]],
  autostart_refused: [["listik/store.py", 'AUTOSTART_QUESTION_PREFIX = "автостарт не выполнен"'],
    ["listik/launcher.py", "{store.AUTOSTART_QUESTION_PREFIX}: "]],
};

test("QUESTION_REASONS: у каждого кода есть образец, код и подпись по образцу", () => {
  assert.deepEqual(Object.keys(SAMPLES).sort(), Object.keys(QUESTION_REASONS).sort());
  for (const [code, text] of Object.entries(SAMPLES)) {
    assert.equal(questionCode(text), code, text);
    assert.equal(questionReason(text), QUESTION_REASONS[code].label, text);
  }
});

test("QUESTION_REASONS: к каждому образцу подходит ровно один шаблон", () => {
  for (const [code, text] of Object.entries(SAMPLES)) {
    const hits = Object.entries(QUESTION_REASONS).filter(([, r]) => r.re.test(text)).map(([c]) => c);
    assert.deepEqual(hits, [code], text);
  }
});

test("QUESTION_REASONS: подписи попарно разные", () => {
  const labels = Object.values(QUESTION_REASONS).map(r => r.label);
  assert.equal(new Set(labels).size, labels.length);
});

test("questionReason: хвост stdout в «не сдал работу» не меняет подпись", () => {
  const text = SAMPLES.not_delivered +
    "\nзадача зависла\nрой: не влита — конфликт\nинтеграционные тесты красные";
  assert.equal(questionReason(text), "работа не сдана");
});

test("questionReason: префикс роя ищется только в начале текста", () => {
  const worker = "Как быть? Видел: рой: не влита — конфликт";
  assert.equal(questionCode(worker), null);
  assert.equal(questionReason(worker), "вопрос воркера");
  assert.equal(questionReason(worker + "\nпо умолчанию: оставить"), "вопрос воркера, есть дефолт");
});

test("questionReason: незнакомый вопрос роя → вопрос роя", () => {
  assert.equal(questionCode("рой: что-то новое"), null);
  assert.equal(questionReason("рой: что-то новое"), "вопрос роя");
});

test("questionReason: ведущие пробелы не мешают", () => {
  assert.equal(questionReason("  рой: не удалось завести рабочее дерево: x"), "нет рабочего дерева");
  assert.equal(questionCode(42), null);
});

test("QUESTION_REASONS: начала текстов Python на месте", () => {
  for (const [code, places] of Object.entries(PYTHON)) {
    assert.ok(Object.hasOwn(QUESTION_REASONS, code), code);
    for (const [rel, literal] of places) {
      assert.ok(source(rel).includes(literal), `${code}: нет «${literal}» в ${rel}`);
    }
  }
});
