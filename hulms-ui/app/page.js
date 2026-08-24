"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

/* ---------- palette ---------- */
const C = {
  bg: "#111418", panel: "#1a1f26", panelSoft: "#20262f", border: "#2c343f",
  text: "#e6e9ed", dim: "#8b95a3", accent: "#7aa2f7", user: "#26466d",
  tool: "#33415c", ok: "#9ece6a", warn: "#e0af68",
};

const spaceIdFor = (course) => (course ? `c${course.id}` : "general");

/* ---------- course display labels ----------
   Names are "Quantum Computing-L1" / "Quantum Computing-R1": the base
   truncation collides, so colliding labels get their section kind appended
   (L=Lecture, R=Recitation, T/Lab already say so), or the raw section code
   when even the kinds collide (e.g. four L-sections of the same course). */
const sectionKind = (s) =>
  s?.startsWith("R") ? "Recitation" : s?.startsWith("T") ? "Lab" : s?.startsWith("L") ? "Lecture" : s || "";

function labelCourses(list, withTerm) {
  const base = (c) => c.name.split("-")[0] + (withTerm ? ` · ${c.term}` : "");
  const section = (c) => (c.name.split("-")[1] || c.code?.split("-")[1] || "").split(",")[0].trim();
  const counts = {};
  for (const c of list) counts[base(c)] = (counts[base(c)] || 0) + 1;
  const kindCounts = {};
  for (const c of list) {
    if (counts[base(c)] > 1) {
      const k = `${base(c)}|${sectionKind(section(c))}`;
      kindCounts[k] = (kindCounts[k] || 0) + 1;
    }
  }
  return list.map((c) => {
    let label = base(c);
    if (counts[label] > 1) {
      const kind = sectionKind(section(c));
      label += ` · ${kindCounts[`${base(c)}|${kind}`] > 1 ? section(c) : kind || section(c)}`;
    }
    return { ...c, label };
  });
}

/* Claude often emits \( \) / \[ \] math delimiters; remark-math wants $ / $$ */
function mathify(src) {
  return src
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, m) => `\n$$\n${m}\n$$\n`)
    .replace(/\\\((.*?)\\\)/g, (_, m) => `$${m}$`);
}

function Md({ text }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {mathify(text)}
      </ReactMarkdown>
    </div>
  );
}

/* ---------- conversation store: multiple named conversations per space ----------
   v2: hulms:v2:<space> = {list: [{id, title, sessionId, messages, updatedAt}], activeId}
   v1 (hulms:<space> = {messages, sessionId}) is migrated on first load. */
const store = {
  load(space) {
    try {
      const v2 = JSON.parse(localStorage.getItem(`hulms:v2:${space}`));
      if (v2 && Array.isArray(v2.list)) return v2;
    } catch {}
    try {
      const v1 = JSON.parse(localStorage.getItem(`hulms:${space}`));
      if (v1 && v1.messages?.length) {
        const conv = {
          id: newId(),
          title: titleFrom(v1.messages) || "Earlier conversation",
          sessionId: v1.sessionId || null,
          messages: v1.messages,
          updatedAt: Date.now(),
        };
        const state = { list: [conv], activeId: conv.id };
        localStorage.setItem(`hulms:v2:${space}`, JSON.stringify(state));
        localStorage.removeItem(`hulms:${space}`);
        return state;
      }
    } catch {}
    return { list: [], activeId: null };
  },
  save(space, state) {
    localStorage.setItem(`hulms:v2:${space}`, JSON.stringify(state));
  },
};

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

const msgText = (m) =>
  m.segments.filter((s) => s.kind === "text").map((s) => s.text).join("\n");

/* context-aware suggestion chips */
const suggestionsFor = (course) =>
  course
    ? [
        "What's due in this course?",
        "Help me study for the upcoming quiz",
        "Run my due reviews, then quiz me on this week's material",
        "Plan my semester from the syllabus and put sessions on my calendar",
        "How am I doing grade-wise — and what do I need for an A?",
        "Index this course's materials so we can search them",
        "Any new announcements in this course?",
      ]
    : [
        "What's due this week across all courses?",
        "Any announcements I missed?",
        "What should I review today?",
        "Plan my week and put study sessions on my calendar",
        "How am I doing across all my courses?",
        "Which course needs my attention most right now?",
      ];

function titleFrom(messages) {
  const first = messages.find((m) => m.role === "user");
  if (!first) return null;
  const t = msgText(first).replace(/\s+/g, " ").trim();
  return t.length > 42 ? t.slice(0, 42) + "…" : t;
}

function timeAgo(ts) {
  if (!ts) return "";
  const mins = Math.round((Date.now() - ts) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

export default function Home() {
  const [courses, setCourses] = useState([]);
  const [courseError, setCourseError] = useState(null);
  const [showCompleted, setShowCompleted] = useState(false);
  const [course, setCourse] = useState(null); // null = General
  const [convs, setConvs] = useState({ list: [], activeId: null });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(null);
  const [panelTab, setPanelTab] = useState(null); // "memory" | "plan" | null
  const [panelText, setPanelText] = useState("");
  const [panelDirty, setPanelDirty] = useState(false);
  const [showIdeas, setShowIdeas] = useState(false);
  const [toast, setToast] = useState(null);
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);

  const space = spaceIdFor(course);
  const activeConv = convs.list.find((c) => c.id === convs.activeId) || null;
  const messages = activeConv?.messages || [];

  useEffect(() => {
    fetch("/api/courses").then((r) => r.json()).then((d) => {
      if (d.error) setCourseError(d.error);
      else setCourses(d.courses || []);
    }).catch((e) => setCourseError(String(e)));
  }, []);

  /* switch space: load its conversations */
  useEffect(() => {
    setConvs(store.load(space));
    setPanelTab(null);
    setPanelDirty(false);
  }, [space]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const persist = useCallback((state) => {
    setConvs(state);
    store.save(space, state);
  }, [space]);

  /* ---------- conversation ops ---------- */
  const selectConv = (id) => { if (!busy) persist({ ...convs, activeId: id }); };

  const newConversation = () => {
    if (busy) return;
    const conv = { id: newId(), title: "New chat", sessionId: null, messages: [], updatedAt: Date.now() };
    persist({ list: [conv, ...convs.list], activeId: conv.id });
  };

  const renameConversation = (id) => {
    const conv = convs.list.find((c) => c.id === id);
    const title = window.prompt("Conversation name:", conv?.title || "");
    if (!title?.trim()) return;
    persist({
      ...convs,
      list: convs.list.map((c) => (c.id === id ? { ...c, title: title.trim() } : c)),
    });
  };

  const deleteConversation = (id) => {
    if (busy) return;
    const conv = convs.list.find((c) => c.id === id);
    if (!window.confirm(`Delete "${conv?.title}"? The chat log is removed (memory.md and plan.md stay).`)) return;
    const list = convs.list.filter((c) => c.id !== id);
    persist({ list, activeId: convs.activeId === id ? (list[0]?.id ?? null) : convs.activeId });
  };

  /* ---------- panel ---------- */
  const openPanel = async (tab) => {
    const r = await fetch(`/api/space?space=${space}&file=${tab}`);
    const d = await r.json();
    setPanelText(d.content ?? "");
    setPanelTab(tab);
    setPanelDirty(false);
  };

  const savePanel = async () => {
    await fetch("/api/space", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ space, file: panelTab, content: panelText }),
    });
    setPanelDirty(false);
  };

  const copyMessage = async (i) => {
    await navigator.clipboard.writeText(msgText(messages[i]));
    setCopied(i);
    setTimeout(() => setCopied(null), 1200);
  };

  const deleteMessage = (i) => {
    if (busy || !activeConv) return;
    const msgs = messages.filter((_, j) => j !== i);
    const conv = { ...activeConv, messages: msgs, updatedAt: Date.now() };
    persist({
      list: convs.list.map((c) => (c.id === conv.id ? conv : c)),
      activeId: convs.activeId,
    });
  };

  const flashToast = (text) => {
    setToast(text);
    setTimeout(() => setToast(null), 4000);
  };

  const uploadFiles = async (fileList) => {
    if (!fileList?.length) return;
    const form = new FormData();
    form.append("space", space);
    if (course) form.append("courseName", `${course.name} (${course.term})`);
    for (const f of fileList) form.append("files", f);
    try {
      const r = await fetch("/api/upload", { method: "POST", body: form });
      const d = await r.json();
      const bits = [];
      if (d.saved?.length) bits.push(`saved ${d.saved.join(", ")} to this space`);
      if (d.rejected?.length) bits.push(`rejected: ${d.rejected.join("; ")}`);
      flashToast(bits.join(" · ") || d.error || "upload failed");
    } catch (e) {
      flashToast(`upload failed: ${e}`);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  /* ---------- send ---------- */
  async function send(overrideText) {
    const text = (overrideText ?? input).trim();
    if (!text || busy) return;
    if (overrideText == null) setInput("");
    setBusy(true);

    // Ensure there is an active conversation to stream into.
    let conv = activeConv;
    let list = convs.list;
    if (!conv) {
      conv = { id: newId(), title: "New chat", sessionId: null, messages: [], updatedAt: Date.now() };
      list = [conv, ...list];
    }

    const msgs = [...conv.messages, { role: "user", segments: [{ kind: "text", text }] }];
    const assistant = { role: "assistant", segments: [] };
    msgs.push(assistant);

    conv = {
      ...conv,
      messages: msgs,
      updatedAt: Date.now(),
      title: conv.title === "New chat" ? (titleFrom(msgs) || conv.title) : conv.title,
    };
    list = list.map((c) => (c.id === conv.id ? conv : c));
    persist({ list, activeId: conv.id });

    const update = () => {
      conv = { ...conv, messages: [...msgs.slice(0, -1), { ...assistant, segments: [...assistant.segments] }], updatedAt: Date.now() };
      setConvs((prev) => ({
        list: prev.list.map((c) => (c.id === conv.id ? conv : c)),
        activeId: prev.activeId,
      }));
    };
    const appendText = (t) => {
      const last = assistant.segments[assistant.segments.length - 1];
      if (last?.kind === "text" && !last.final) last.text += t;
      else assistant.segments.push({ kind: "text", text: t });
      update();
    };

    let sid = conv.sessionId;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text, spaceId: space, sessionId: sid,
          courseName: course ? `${course.name} (${course.term})` : null,
        }),
        signal: controller.signal,
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop();
        for (const evt of events) {
          const dataLine = evt.split("\n").find((l) => l.startsWith("data: "));
          if (!dataLine) continue;
          let obj;
          try { obj = JSON.parse(dataLine.slice(6)); } catch { continue; }

          if (obj.type === "system" && obj.subtype === "init") {
            sid = obj.session_id || sid;
          } else if (obj.type === "stream_event") {
            const ev = obj.event || {};
            if (ev.type === "content_block_delta" && ev.delta?.type === "text_delta") {
              appendText(ev.delta.text);
            } else if (ev.type === "content_block_start" && ev.content_block?.type === "tool_use") {
              const last = assistant.segments[assistant.segments.length - 1];
              if (last?.kind === "text") last.final = true;
              assistant.segments.push({ kind: "tool", name: ev.content_block.name });
              update();
            }
          } else if (obj.type === "result") {
            sid = obj.session_id || sid;
            if (obj.is_error && obj.result) appendText(`\n\n> [error] ${obj.result}`);
          } else if (obj.type === "spawn_error") {
            appendText(`\n\n> [claude failed to run: ${obj.error || "exit " + obj.code}]`);
          }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") appendText(`\n\n> [connection error: ${e}]`);
    } finally {
      abortRef.current = null;
      setBusy(false);
      conv = { ...conv, sessionId: sid };
      setConvs((prev) => {
        const state = {
          list: prev.list.map((c) => (c.id === conv.id ? conv : c)),
          activeId: prev.activeId,
        };
        store.save(space, state);
        return state;
      });
    }
  }

  const retry = () => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (lastUser) send(msgText(lastUser));
  };

  const active = labelCourses(courses.filter((c) => c.state === "active"), false);
  const completed = labelCourses(courses.filter((c) => c.state === "completed"), true);

  const sideItem = (key, label, selected, onClick, dim) => (
    <div key={key} onClick={onClick} style={{
      padding: "7px 12px", borderRadius: 8, cursor: "pointer", fontSize: 13,
      color: dim ? C.dim : C.text, marginBottom: 2, lineHeight: 1.3,
      background: selected ? C.panelSoft : "transparent",
      borderLeft: selected ? `3px solid ${C.accent}` : "3px solid transparent",
    }}>{label}</div>
  );

  const iconBtn = (label, onClick, title) => (
    <button key={label} onClick={onClick} title={title} style={{
      background: "transparent", color: C.dim, border: `1px solid ${C.border}`,
      borderRadius: 5, padding: "1px 8px", cursor: "pointer", fontSize: 11,
    }}>{label}</button>
  );

  const sorted = [...convs.list].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));

  return (
    <div style={{ display: "flex", height: "100vh", background: C.bg, color: C.text }}>
      {/* courses sidebar */}
      <div style={{ width: 240, borderRight: `1px solid ${C.border}`, padding: 12, overflowY: "auto", flexShrink: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 15, margin: "4px 0 14px 4px" }}>
          HULMS <span style={{ color: C.accent }}>Assistant</span>
        </div>
        {sideItem("general", "General", !course, () => !busy && setCourse(null))}
        <div style={{ color: C.dim, fontSize: 11, margin: "14px 4px 6px", textTransform: "uppercase", letterSpacing: 1 }}>This semester</div>
        {active.map((c) => sideItem(c.id, c.label, course?.id === c.id, () => !busy && setCourse(c)))}
        <div onClick={() => setShowCompleted(!showCompleted)}
             style={{ color: C.dim, fontSize: 11, margin: "14px 4px 6px", cursor: "pointer", textTransform: "uppercase", letterSpacing: 1 }}>
          {showCompleted ? "▾" : "▸"} Past courses ({completed.length})
        </div>
        {showCompleted && completed.map((c) =>
          sideItem(c.id, c.label, course?.id === c.id, () => !busy && setCourse(c), true))}
        {courseError && <div style={{ color: C.warn, fontSize: 12, marginTop: 10 }}>courses failed: {courseError}</div>}
      </div>

      {/* conversations column */}
      <div style={{ width: 215, borderRight: `1px solid ${C.border}`, display: "flex", flexDirection: "column", flexShrink: 0 }}>
        <div style={{ padding: 10 }}>
          <button onClick={newConversation} disabled={busy} style={{
            width: "100%", background: C.panelSoft, color: C.text, border: `1px solid ${C.border}`,
            borderRadius: 8, padding: "8px 0", cursor: busy ? "default" : "pointer", fontSize: 13,
          }}>＋ new chat</button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "0 8px 8px" }}>
          {sorted.length === 0 && (
            <div style={{ color: C.dim, fontSize: 12, textAlign: "center", marginTop: 24 }}>no chats yet</div>
          )}
          {sorted.map((c) => (
            <div key={c.id} onClick={() => selectConv(c.id)} className="convrow" style={{
              padding: "8px 10px", borderRadius: 8, cursor: "pointer", marginBottom: 3,
              background: c.id === convs.activeId ? C.panelSoft : "transparent",
              borderLeft: c.id === convs.activeId ? `3px solid ${C.accent}` : "3px solid transparent",
            }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <div style={{ flex: 1, fontSize: 12.5, lineHeight: 1.35, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                  {c.title}
                </div>
                <div style={{ color: C.dim, fontSize: 10, flexShrink: 0 }}>{timeAgo(c.updatedAt)}</div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 3 }}>
                <span onClick={(e) => { e.stopPropagation(); renameConversation(c.id); }}
                      title="Rename" style={{ color: C.dim, fontSize: 11, cursor: "pointer" }}>✎</span>
                <span onClick={(e) => { e.stopPropagation(); deleteConversation(c.id); }}
                      title="Delete" style={{ color: C.dim, fontSize: 11, cursor: "pointer" }}>✕</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* main */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ padding: "10px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ fontWeight: 600, flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {course ? (course.label || course.name.split("-")[0]) : "General"}
            {activeConv && <span style={{ color: C.dim, fontWeight: 400 }}> · {activeConv.title}</span>}
            {activeConv?.sessionId && <span style={{ color: C.dim, fontWeight: 400, fontSize: 12 }}> · session continues</span>}
          </div>
          {["memory", "plan"].map((t) => (
            <button key={t} onClick={() => (panelTab === t ? setPanelTab(null) : openPanel(t))}
              style={{ background: panelTab === t ? C.tool : C.panelSoft, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 12 }}>
              {t}
            </button>
          ))}
          <button
            title="Open this course's folder — drop syllabi (e.g. Simple Syllabus PDF exports) or any files here for the coach to read"
            onClick={() => fetch("/api/open", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ space, courseName: course ? `${course.name} (${course.term})` : null }),
            })}
            style={{ background: C.panelSoft, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 12px", cursor: "pointer", fontSize: 12 }}>
            📁 folder
          </button>
        </div>

        <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
          {/* messages */}
          <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "18px 0" }}>
            <div style={{ maxWidth: 860, margin: "0 auto", padding: "0 22px" }}>
              {messages.length === 0 && (
                <div style={{ marginTop: 50, textAlign: "center" }}>
                  <div style={{ color: C.dim, fontSize: 14, marginBottom: 18 }}>
                    {course ? `Ask about ${course.label || course.name.split("-")[0]} — or pick one:` : "Ask about anything across your courses — or pick one:"}
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", maxWidth: 620, margin: "0 auto" }}>
                    {suggestionsFor(course).map((s) => (
                      <button key={s} onClick={() => send(s)} disabled={busy} style={{
                        background: C.panel, color: C.text, border: `1px solid ${C.border}`,
                        borderRadius: 16, padding: "7px 14px", cursor: "pointer", fontSize: 12.5,
                        textAlign: "left",
                      }}>{s}</button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m, i) => (
                <div key={i} style={{ margin: "14px 0", display: "flex", flexDirection: "column", alignItems: m.role === "user" ? "flex-end" : "stretch" }}>
                  <div style={{
                    maxWidth: m.role === "user" ? "78%" : "100%",
                    borderRadius: 10, padding: "10px 16px", fontSize: 14,
                    background: m.role === "user" ? C.user : C.panel,
                    border: `1px solid ${C.border}`,
                  }}>
                    {m.segments.map((s, j) =>
                      s.kind === "tool" ? (
                        <span key={j} style={{ display: "inline-block", background: C.tool, borderRadius: 5, padding: "2px 8px", fontSize: 11, color: C.dim, margin: "4px 6px 4px 0" }}>
                          ⚙ {s.name.replace("mcp__hulms__", "")}
                        </span>
                      ) : m.role === "assistant" ? (
                        <Md key={j} text={s.text} />
                      ) : (
                        <div key={j} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{s.text}</div>
                      )
                    )}
                    {m.role === "assistant" && busy && i === messages.length - 1 && (
                      <span style={{ color: C.accent }}>▍</span>
                    )}
                  </div>
                  {!(busy && i === messages.length - 1) && (
                    <div style={{ display: "flex", gap: 6, marginTop: 5 }}>
                      {m.role === "assistant" && msgText(m) &&
                        iconBtn(copied === i ? "copied ✓" : "copy", () => copyMessage(i), "Copy message text")}
                      {m.role === "assistant" && i === messages.length - 1 && msgText(m) &&
                        iconBtn("↻ retry", retry, "Ask the same question again")}
                      {iconBtn("✕", () => deleteMessage(i), "Remove this message from the log (the session itself still remembers it)")}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* memory / plan panel */}
          {panelTab && (
            <div style={{ width: 380, borderLeft: `1px solid ${C.border}`, display: "flex", flexDirection: "column", flexShrink: 0 }}>
              <div style={{ padding: "8px 12px", fontSize: 12, color: C.dim, display: "flex", alignItems: "center" }}>
                <span style={{ flex: 1 }}>{space}/{panelTab}.md</span>
                {panelDirty && <button onClick={savePanel} style={{ background: C.ok, color: "#111", border: 0, borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 12 }}>save</button>}
              </div>
              <textarea value={panelText}
                onChange={(e) => { setPanelText(e.target.value); setPanelDirty(true); }}
                style={{ flex: 1, background: C.panel, color: C.text, border: 0, outline: "none", padding: 12, fontSize: 13, fontFamily: "Consolas, monospace", resize: "none" }} />
            </div>
          )}
        </div>

        {/* input */}
        <div style={{ padding: 14, borderTop: `1px solid ${C.border}` }}>
          {showIdeas && messages.length > 0 && (
            <div style={{ maxWidth: 860, margin: "0 auto 10px", display: "flex", flexWrap: "wrap", gap: 6 }}>
              {suggestionsFor(course).map((s) => (
                <button key={s} onClick={() => { setShowIdeas(false); send(s); }} disabled={busy} style={{
                  background: C.panel, color: C.dim, border: `1px solid ${C.border}`,
                  borderRadius: 14, padding: "5px 12px", cursor: "pointer", fontSize: 12,
                }}>{s}</button>
              ))}
            </div>
          )}
          {toast && (
            <div style={{ maxWidth: 860, margin: "0 auto 8px", color: C.ok, fontSize: 12.5 }}>
              {toast}
            </div>
          )}
          <div style={{ maxWidth: 860, margin: "0 auto", display: "flex", gap: 10 }}>
            <input ref={fileInputRef} type="file" multiple style={{ display: "none" }}
                   onChange={(e) => uploadFiles(Array.from(e.target.files || []))} />
            <button onClick={() => fileInputRef.current?.click()}
              title="Upload files into this course's space (syllabi, notes, PDFs) — the coach can read and search them"
              style={{ background: C.panelSoft, color: C.text, border: `1px solid ${C.border}`, borderRadius: 8, padding: "0 13px", cursor: "pointer", fontSize: 16 }}>
              📎
            </button>
            <button onClick={() => setShowIdeas(!showIdeas)}
              title="Suggestions — things worth asking"
              style={{ background: showIdeas ? C.tool : C.panelSoft, color: C.text, border: `1px solid ${C.border}`, borderRadius: 8, padding: "0 13px", cursor: "pointer", fontSize: 16 }}>
              💡
            </button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder={busy ? "thinking…" : "Message (Enter to send, Shift+Enter for newline)"}
              rows={2}
              style={{ flex: 1, background: C.panel, color: C.text, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", fontSize: 14, resize: "none", outline: "none" }}
            />
            {busy ? (
              <button onClick={() => abortRef.current?.abort()} style={{ background: C.warn, color: "#111", border: 0, borderRadius: 8, padding: "0 18px", cursor: "pointer", fontWeight: 600 }}>stop</button>
            ) : (
              <button onClick={() => send()} style={{ background: C.accent, color: "#111", border: 0, borderRadius: 8, padding: "0 18px", cursor: "pointer", fontWeight: 600 }}>send</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
