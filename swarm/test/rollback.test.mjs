import {test} from "node:test";
import assert from "node:assert/strict";
import {
  FREEZE_MARK, SCOPE_MARK, LIMIT_PREFIX, LAUNCH_AUTHOR, LAUNCH_PREFIX,
  freezeHistory, scopeDrift, rollbackVerdict, limitText,
} from "../rollback.mjs";

const SWARM = "agent:listik-swarm";

function at(hms) {
  return `2026-09-21T${hms}Z`;
}

function freeze(hms, owner, files, generation = 2) {
  const payload = {owner, generation};
  if (files !== undefined) payload.files = files;
  return {
    author: SWARM,
    kind: "journal",
    text: `${FREEZE_MARK} ${JSON.stringify(payload)}`,
    created_at: at(hms),
  };
}

function launch(hms, generation) {
  return {
    author: LAUNCH_AUTHOR,
    kind: "journal",
    text: `${LAUNCH_PREFIX}r, pid 1, лог /l, поколение ${generation}, запуск abc`,
    created_at: at(hms),
  };
}

function ownersCard(owners) {
  return {
    comments: owners.map((owner, i) => freeze(`10:0${i}:00`, owner, [`${owner}.txt`], i + 2)),
    write_scope: ["q/"],
  };
}

test("маркеры совпадают со swarm_watch и журналом запуска", () => {
  assert.equal(FREEZE_MARK, "рой: заморожена:");
  assert.equal(SCOPE_MARK, "рой: вне write_scope:");
  assert.equal(LIMIT_PREFIX, "рой: предел откатов");
  assert.equal(LAUNCH_AUTHOR, "agent:listik");
  assert.equal(LAUNCH_PREFIX, "автостарт: маршрут ");
});

test("пустая карточка: нет истории, вердикт нулевой, declared = write_scope", () => {
  const card = {write_scope: ["q/"]};
  assert.deepEqual(freezeHistory(card), []);
  assert.deepEqual(freezeHistory({comments: []}), []);
  const verdict = rollbackVerdict(card, 2);
  assert.equal(verdict.count, 0);
  assert.equal(verdict.total, 0);
  assert.equal(verdict.exceeded, false);
  assert.equal(verdict.minutes, 0);
  assert.equal(verdict.minutesTotal, 0);
  assert.equal(verdict.last, null);
  assert.deepEqual(verdict.drift.files, []);
  assert.deepEqual(verdict.drift.declared, ["q/"]);
});

test("freezeHistory: 7.5 мин округляются до 8, все пять полей", () => {
  const card = {
    comments: [
      {
        author: "agent:listik",
        kind: "journal",
        text: "автостарт: маршрут r, pid 1, лог /l, поколение 1, запуск abc",
        created_at: "2026-09-21T10:00:00Z",
      },
      {
        author: SWARM,
        kind: "journal",
        text: `${FREEZE_MARK} ${JSON.stringify({owner: "t1", files: ["a.txt"], generation: 2})}`,
        created_at: "2026-09-21T10:07:30Z",
      },
    ],
  };
  assert.deepEqual(freezeHistory(card), [{
    owner: "t1",
    files: ["a.txt"],
    generation: 2,
    at: "2026-09-21T10:07:30Z",
    minutes: 8,
  }]);
});

test("freezeHistory: нет журнала запуска — minutes null, сумма вердикта 0", () => {
  const card = {comments: [freeze("10:07:30", "t1", ["a.txt"], 2)]};
  const history = freezeHistory(card);
  assert.equal(history.length, 1);
  assert.equal(history[0].minutes, null);
  const verdict = rollbackVerdict(card, 2);
  assert.equal(verdict.minutes, 0);
  assert.equal(verdict.minutesTotal, 0);
});

test("freezeHistory: нет files — пустой массив", () => {
  const card = {comments: [freeze("10:07:30", "t1", undefined, 2)]};
  assert.deepEqual(freezeHistory(card)[0].files, []);
});

test("rollbackVerdict: порог, сумма только известных минут", () => {
  const three = ownersCard(["t1", "t2", "t3"]);
  const atTwo = rollbackVerdict(three, 2);
  assert.equal(atTwo.count, 3);
  assert.equal(atTwo.total, 3);
  assert.equal(atTwo.exceeded, true);
  assert.equal(atTwo.last.owner, "t3");
  assert.equal(rollbackVerdict(three, 3).exceeded, false);
  assert.equal(rollbackVerdict(three, null).exceeded, false);
  assert.equal(rollbackVerdict(three, undefined).exceeded, false);
  assert.equal(rollbackVerdict(ownersCard(["t1"]), 0).exceeded, true);

  const mixed = {
    comments: [
      launch("10:00:00", 1),
      freeze("10:07:30", "t1", ["a.txt"], 2),
      freeze("10:20:00", "t2", ["b.txt"], 9),
    ],
  };
  const verdict = rollbackVerdict(mixed, 2);
  assert.equal(verdict.freezes[0].minutes, 8);
  assert.equal(verdict.freezes[1].minutes, null);
  assert.equal(verdict.minutes, 8);
  assert.equal(verdict.minutesTotal, 8);
});

test("окно: парковка роем сбрасывает счёт, чужой автор и journal — нет", () => {
  const freezes = [
    freeze("10:00:00", "t1", ["a.txt"], 2),
    freeze("10:01:00", "t2", ["b.txt"], 3),
    freeze("10:03:00", "t3", ["c.txt"], 4),
  ];
  const park = {
    author: SWARM,
    kind: "question",
    text: `${LIMIT_PREFIX} — задача заморожена 2 раз`,
    created_at: at("10:02:00"),
  };
  const parked = rollbackVerdict({comments: [freezes[0], freezes[1], park, freezes[2]]}, 2);
  assert.equal(parked.count, 1);
  assert.equal(parked.total, 3);
  assert.equal(parked.window[0].owner, "t3");
  assert.equal(parked.exceeded, false);

  const otherAuthor = {...park, author: "dmitry"};
  const byHuman = rollbackVerdict({comments: [freezes[0], freezes[1], otherAuthor, freezes[2]]}, 2);
  assert.equal(byHuman.count, 3);

  const asJournal = {...park, kind: "journal"};
  const byJournal = rollbackVerdict({comments: [freezes[0], freezes[1], asJournal, freezes[2]]}, 2);
  assert.equal(byJournal.count, 3);
});

test("freezeHistory и scopeDrift читают только comments и write_scope", () => {
  const card = {
    comments: [freeze("10:00:00", "t1", ["a.txt"], 2)],
    write_scope: ["q/"],
  };
  assert.equal(freezeHistory(card)[0].owner, "t1");
  assert.deepEqual(scopeDrift(card), {files: [], declared: ["q/"]});
  assert.equal(rollbackVerdict(card, 2).count, 1);
});

test("чужой автор не считается, битый JSON пропускается", () => {
  const card = {
    comments: [
      {
        author: "dmitry",
        kind: "journal",
        text: `${FREEZE_MARK} ${JSON.stringify({owner: "nope", files: ["z.txt"], generation: 1})}`,
        created_at: at("10:00:00"),
      },
      {author: SWARM, kind: "journal", text: `${FREEZE_MARK} {`, created_at: at("10:01:00")},
      freeze("10:02:00", "t1", ["a.txt"], 2),
    ],
  };
  const history = freezeHistory(card);
  assert.equal(history.length, 1);
  assert.equal(history[0].owner, "t1");
});

test("scopeDrift: объединение files, declared последней записи", () => {
  const card = {
    write_scope: ["q/"],
    comments: [
      {
        author: SWARM,
        text: `${SCOPE_MARK} ${JSON.stringify({files: ["a.txt", "c.txt"], declared: ["x/", "y/"]})}`,
        created_at: at("10:05:00"),
      },
      {
        author: SWARM,
        text: `${SCOPE_MARK} ${JSON.stringify({files: ["b.txt", "a.txt"], declared: ["x/"]})}`,
        created_at: at("10:01:00"),
      },
    ],
  };
  assert.deepEqual(scopeDrift(card), {files: ["a.txt", "b.txt", "c.txt"], declared: ["x/", "y/"]});
  assert.deepEqual(scopeDrift({write_scope: ["q/"]}), {files: [], declared: ["q/"]});
});

test("limitText: состав окна, расхождение и пустой drift", () => {
  const drifted = {
    comments: [
      freeze("10:00:00", "t1", ["a.txt"], 2),
      freeze("10:01:00", "t2", ["b.txt"], 3),
      freeze("10:02:00", "t3", ["a.txt"], 4),
      {
        author: SWARM,
        text: `${SCOPE_MARK} ${JSON.stringify({files: ["c.txt"], declared: ["x/"]})}`,
        created_at: at("10:03:00"),
      },
    ],
  };
  const text = limitText("t9", rollbackVerdict(drifted, 2), 2);
  assert.equal(text.startsWith(LIMIT_PREFIX), true);
  assert.ok(text.includes("3 раз"));
  assert.ok(text.includes("max_freezes = 2"));
  assert.ok(text.includes("t1: a.txt"));
  assert.ok(text.includes("t2: b.txt"));
  assert.ok(text.includes("t3: a.txt"));
  assert.ok(text.includes("вытесняли: t1: a.txt; t2: b.txt; t3: a.txt"));
  assert.ok(text.includes("c.txt"));
  assert.ok(text.includes("listik needs-owner t9 --clear"));
  assert.ok(text.includes("~0 мин"));

  const clean = limitText("t9", rollbackVerdict({
    comments: [
      freeze("10:00:00", "t1", ["a.txt"], 2),
      freeze("10:01:00", "t2", ["b.txt"], 3),
      freeze("10:02:00", "t3", ["a.txt"], 4),
    ],
  }, 2), 2);
  assert.ok(clean.includes("вне объявленного write_scope: нет"));
});

test("limitText: заморозка без файлов", () => {
  const card = {comments: [freeze("10:00:00", "t1", [], 2)]};
  const text = limitText("t9", rollbackVerdict(card, 0), 0);
  assert.ok(text.includes("t1: (файлы не записаны)"));
});
