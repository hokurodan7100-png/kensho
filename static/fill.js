/*
 * 懸賞フォーム自動入力スクリプト
 *
 * window.__KENSHO_PROFILE にプロフィールを入れてから実行すると、ページ内の
 * 入力欄の名前・ラベル・placeholder などから項目を推測して入力する。
 *   - 送信ボタンは絶対に押さない（最終確認と送信は利用者が行う）
 *   - 規約同意チェックボックス・パスワード・ファイル欄・CAPTCHA には触れない
 *   - 入力した欄は緑、推測できなかった欄は黄色の枠で示す
 */
(function () {
  "use strict";
  const P = window.__KENSHO_PROFILE || {};
  // filled/unknown: 件数、fields: 入力した欄、unknownFields: 入力できなかった欄、
  // checkboxes: チェックボックス（規約同意など）、captcha/login: 画像認証・ログイン欄の有無
  const RESULT = { filled: 0, unknown: 0, fields: [], unknownFields: [], checkboxes: [], captcha: false, login: false };

  // ---------- 文字種変換 ----------
  const kataToHira = (s) => (s || "").replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
  const toHalfDigits = (s) => (s || "").replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
  const digits = (s) => toHalfDigits(s).replace(/\D/g, "");

  // ---------- 欄の説明文を集める ----------
  function labelText(el) {
    const parts = [];
    if (el.id) {
      document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach((l) => parts.push(l.textContent));
    }
    const wrap = el.closest("label");
    if (wrap) parts.push(wrap.textContent);
    if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) parts.push(n.textContent); });
    // 表形式（<th>ラベル</th><td><input></td>）や <dt>/<dd> の形式
    const cell = el.closest("td, dd");
    if (cell) {
      const prev = cell.previousElementSibling;
      if (prev && /^(TH|DT|TD)$/.test(prev.tagName)) parts.push(prev.textContent);
      else if (cell.tagName === "TD") { const th = cell.parentElement && cell.parentElement.querySelector("th"); if (th) parts.push(th.textContent); }
    }
    // 直前の見出し的なテキスト（div 構成のフォーム向け）
    if (!parts.length) {
      let node = el.parentElement;
      for (let i = 0; i < 3 && node && !parts.length; i++, node = node.parentElement) {
        const t = Array.from(node.childNodes)
          .filter((n) => n.nodeType === 3 || (n.nodeType === 1 && !n.matches("input,select,textarea") && !n.querySelector("input,select,textarea")))
          .map((n) => n.textContent).join(" ").trim();
        if (t && t.length < 40) parts.push(t);
      }
    }
    return parts.join(" ").replace(/必須|任意|[*＊※]/g, " ").replace(/[（(【\[]\s*[）)】\]]/g, "").replace(/\s+/g, " ").trim();
  }

  function describe(el) {
    const attrs = ["name", "id", "placeholder", "autocomplete", "title", "class"].map((a) => el.getAttribute(a) || "").join(" ");
    return { label: labelText(el), attrs: attrs, all: (labelText(el) + " " + attrs).toLowerCase() };
  }

  // ---------- 項目の推定ルール（上から順に評価） ----------
  const AUTOCOMPLETE = {
    "family-name": "last_name", "given-name": "first_name", "name": "full_name",
    "email": "email", "tel": "phone", "tel-national": "phone", "postal-code": "zip",
    "address-level1": "prefecture", "address-level2": "city", "address-line1": "address1",
    "address-line2": "address2", "street-address": "address_full",
    "bday-year": "birth_year", "bday-month": "birth_month", "bday-day": "birth_day", "bday": "birthday",
    "sex": "gender",
  };
  const RULES = [
    ["email", /mail|メール/],
    ["last_kana", /(セイ|(^|\s)姓.*(カナ|かな|フリガナ|ふりがな))|((kana|furigana|ruby|yomi).*(sei|last|family))|((sei|last|family).*(kana|furigana|ruby|yomi))/],
    ["first_kana", /(メイ|(^|\s)名.*(カナ|かな|フリガナ|ふりがな))|((kana|furigana|ruby|yomi).*(mei|first|given))|((mei|first|given).*(kana|furigana|ruby|yomi))/],
    ["full_kana", /フリガナ|ふりがな|カナ|かな|kana|furigana|yomi/],
    ["zip", /郵便|〒|zip|postal|postcode|yubin/],
    ["prefecture", /都道府県|pref|state|region/],
    ["phone", /電話|携帯|tel|phone|mobile/],
    ["birth_year", /(生年月日|誕生|birth|bday|dob).*(年|year)|^年$|byear|birth_?y\b/],
    ["birth_month", /(生年月日|誕生|birth|bday|dob).*(月|month)|^月$|bmonth|birth_?m\b/],
    ["birth_day", /(生年月日|誕生|birth|bday|dob).*(日|day)|^日$|bday_?d|birth_?d\b/],
    ["birthday", /生年月日|誕生日|birth|bday|dob/],
    ["age", /年齢|age\b/],
    ["gender", /性別|gender|sex/],
    ["job", /職業|job|occupation/],
    ["address2", /建物|マンション|ビル|部屋|building|apartment|address_?2|addr_?2/],
    ["address1", /番地|丁目|street|address_?1|addr_?1/],
    ["city", /市区町村|市町村|city|town/],
    ["address_full", /住所|所在地|address|addr|jusho/],
    ["last_name", /(^|\s)(姓|名字|苗字)(?!名)|last.?name|family.?name|lname|\bsei\b/],
    ["first_name", /(^|\s)名(\s|$|[（(])|first.?name|given.?name|fname|\bmei\b/],
    ["full_name", /氏名|お名前|名前|name|onamae/],
  ];

  function classify(el) {
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/).pop();
    if (AUTOCOMPLETE[ac]) return AUTOCOMPLETE[ac];
    if (el.type === "email") return "email";
    if (el.type === "tel") return "phone";
    const d = describe(el);
    const label = d.label.toLowerCase();
    for (const [key, re] of RULES) if (re.test(label)) return key;
    for (const [key, re] of RULES) if (re.test(d.all)) return key;
    return null;
  }

  // ---------- 値の決定 ----------
  const wantsHiragana = (el) => {
    const d = describe(el).all + " " + (el.placeholder || "");
    return /ひらがな|ふりがな/.test(d) || (/[ぁ-ゖ]/.test(el.placeholder || "") && !/[ァ-ヶ]/.test(el.placeholder || ""));
  };
  function age() {
    const y = +P.birth_year, m = +P.birth_month || 1, d = +P.birth_day || 1;
    if (!y) return "";
    const t = new Date();
    let a = t.getFullYear() - y;
    if (t.getMonth() + 1 < m || (t.getMonth() + 1 === m && t.getDate() < d)) a--;
    return String(a);
  }
  const pad = (n) => String(n).padStart(2, "0");

  // 分割入力欄（電話 3 分割、郵便 2 分割、姓/名 2 分割など）の値
  const SPLIT = {
    phone: () => (P.phone || "").split(/[-ー－]/),
    zip: () => (P.zip || "").split(/[-ー－]/),
    full_name: () => [P.last_name, P.first_name],
    full_kana: () => [P.last_kana, P.first_kana],
    birthday: () => [P.birth_year, P.birth_month, P.birth_day],
  };

  function valueFor(key, el) {
    const kana = (s) => (wantsHiragana(el) ? kataToHira(s) : s);
    switch (key) {
      case "last_kana": return kana(P.last_kana);
      case "first_kana": return kana(P.first_kana);
      case "full_kana": return kana([P.last_kana, P.first_kana].filter(Boolean).join(" "));
      case "full_name": return [P.last_name, P.first_name].filter(Boolean).join(" ");
      case "phone": return (el.maxLength > 0 && el.maxLength < 12) || /ハイフンなし|ハイフン無し|半角数字のみ/.test(describe(el).all) ? digits(P.phone) : P.phone;
      case "zip": return (el.maxLength > 0 && el.maxLength < 8) || /ハイフンなし|ハイフン無し/.test(describe(el).all) ? digits(P.zip) : P.zip;
      case "address_full": return [P.prefecture, P.city, P.address1, P.address2].filter(Boolean).join("");
      case "birthday":
        if (!P.birth_year) return "";
        return el.type === "date" ? `${P.birth_year}-${pad(P.birth_month)}-${pad(P.birth_day)}` : `${P.birth_year}/${pad(P.birth_month)}/${pad(P.birth_day)}`;
      case "age": return age();
      case "email_confirm": return P.email;
      default: return P[key] || "";
    }
  }

  // ---------- 入力 ----------
  function setNative(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value); // React などの管理下の入力欄でも反映されるように
    ["input", "change", "blur"].forEach((t) => el.dispatchEvent(new Event(t, { bubbles: true })));
  }

  function fillSelect(el, value) {
    if (!value) return false;
    const v = String(value);
    const opts = Array.from(el.options);
    const norm = (s) => toHalfDigits(s).replace(/\s/g, "");
    const hit = opts.find((o) => norm(o.value) === norm(v) || norm(o.text) === norm(v))
      || (/^\d+$/.test(v) && opts.find((o) => digits(o.text) === String(+v) || digits(o.value) === String(+v) || digits(o.text) === pad(v)))
      || opts.find((o) => norm(o.text).includes(norm(v)) && o.value !== "");
    if (!hit) return false;
    setNative(el, hit.value);
    return true;
  }

  function mark(el, ok) {
    el.style.outline = ok ? "3px solid #22c55e" : "3px dashed #eab308";
    el.style.outlineOffset = "1px";
  }

  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const SKIP_TYPES = /^(hidden|submit|button|reset|image|password|file|checkbox|range|color|search)$/;
  const isCaptcha = (el) => /captcha|recaptcha|認証コード|画像の文字/i.test(describe(el).all);
  const CAPTCHA_SEL = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], iframe[src*='challenges.cloudflare'], .g-recaptcha, .h-captcha, .cf-turnstile, img[src*='captcha' i]";

  function isRequired(el) {
    if (el.required || el.getAttribute("aria-required") === "true") return true;
    const row = el.closest("tr, dl, li, fieldset, .form-group, .form-item") || el.parentElement;
    const t = row ? row.textContent.slice(0, 200) : "";
    return /必須|[*＊]/.test(t) && !/任意/.test(t);
  }
  const shortLabel = (el) => (labelText(el) || el.getAttribute("placeholder") || el.name || "").replace(/\s+/g, " ").slice(0, 40);
  function shownValue(el) {
    if (el.tagName === "SELECT") { const o = el.options[el.selectedIndex]; return o ? o.text.trim() : ""; }
    return el.value;
  }

  function run() {
    RESULT.captcha = !!Array.from(document.querySelectorAll(CAPTCHA_SEL)).find(visible)
      || !!Array.from(document.querySelectorAll("input")).find((el) => visible(el) && isCaptcha(el));
    RESULT.login = !!Array.from(document.querySelectorAll("input[type=password]")).find(visible);
    Array.from(document.querySelectorAll("input[type=checkbox]")).filter((el) => !el.disabled && visible(el)).forEach((el, i) => {
      el.setAttribute("data-kensho-cb", String(i));
      const label = shortLabel(el) || (el.closest("tr, li, div") || el.parentElement).textContent.trim().slice(0, 40);
      RESULT.checkboxes.push({ index: i, label, required: isRequired(el), checked: el.checked, agree: /同意|規約|承諾|agree|consent/i.test(label + " " + el.name) });
    });
    const fields = Array.from(document.querySelectorAll("input, select, textarea")).filter(
      (el) => !el.disabled && !el.readOnly && visible(el) && !SKIP_TYPES.test(el.type) && !isCaptcha(el)
    );

    // 性別などのラジオボタンはグループ単位で扱う
    const radioGroups = new Map();
    const singles = [];
    for (const el of fields) {
      if (el.type === "radio") {
        if (!radioGroups.has(el.name)) radioGroups.set(el.name, []);
        radioGroups.get(el.name).push(el);
      } else singles.push(el);
    }

    // 種類を判定し、同じ種類が連続していれば分割入力欄とみなす
    const typed = singles.map((el) => ({ el, key: classify(el) }));
    let emailSeen = false;
    for (let i = 0; i < typed.length; i++) {
      const t = typed[i];
      if (t.key === "email") { if (emailSeen || /確認|confirm|again|再入力/.test(describe(t.el).all)) t.key = "email_confirm"; emailSeen = true; }
    }
    for (let i = 0; i < typed.length; ) {
      const { key } = typed[i];
      let j = i;
      while (j + 1 < typed.length && typed[j + 1].key === key) j++;
      const run = typed.slice(i, j + 1);
      const parts = key && SPLIT[key] ? SPLIT[key]() : null;
      if (run.length > 1 && parts && parts.length === run.length) run.forEach((t, k) => (t.value = parts[k]));
      i = j + 1;
    }

    for (const t of typed) {
      const { el } = t;
      if (!t.key) {
        if (!el.value || el.tagName === "SELECT" && !el.value) { mark(el, false); RESULT.unknown++; RESULT.unknownFields.push({ label: shortLabel(el), required: isRequired(el) }); }
        continue;
      }
      if (el.value && el.tagName !== "SELECT") continue; // 既に入力済みの欄は上書きしない
      const value = t.value !== undefined ? t.value : valueFor(t.key, el);
      let ok = false;
      if (el.tagName === "SELECT") ok = fillSelect(el, value);
      else if (value) { setNative(el, value); ok = true; }
      mark(el, ok);
      if (ok) { RESULT.filled++; RESULT.fields.push({ label: shortLabel(el), value: shownValue(el) }); }
      else { RESULT.unknown++; RESULT.unknownFields.push({ label: shortLabel(el), required: isRequired(el) }); }
    }

    for (const [, radios] of radioGroups) {
      const key = classify(radios[0]);
      const want = key === "gender" ? P.gender : key === "job" ? P.job : null;
      if (radios.some((r) => r.checked)) continue;
      const groupLabel = (() => { const cell = radios[0].closest("td, dd"); const prev = cell && cell.previousElementSibling; return (prev ? prev.textContent : radios[0].name).replace(/必須|任意|[*＊※]/g, "").replace(/[（(【\[]\s*[）)】\]]/g, "").trim().slice(0, 40); })();
      if (!want) { RESULT.unknown++; RESULT.unknownFields.push({ label: groupLabel, required: radios.some(isRequired) }); continue; }
      const short = want.replace(/性$/, "");
      const hit = radios.find((r) => { const s = labelText(r) + " " + r.value; return s.includes(want) || s.includes(short) || (short === "男" && /male\b|^m$/i.test(r.value)) || (short === "女" && /female|^f$/i.test(r.value)); });
      if (hit) { hit.click(); mark(hit, true); RESULT.filled++; RESULT.fields.push({ label: groupLabel, value: (labelText(hit) || hit.value).trim() }); }
      else { RESULT.unknown++; RESULT.unknownFields.push({ label: groupLabel, required: radios.some(isRequired) }); }
    }

    // 同じページで 2 回目以降に実行されたときは、入力件数を合計して表示する
    const prev = window.__KENSHO_RESULT;
    if (prev) RESULT.filled += prev.filled;
    if (!window.__KENSHO_NOBANNER) banner();
    return RESULT;
  }

  function banner() {
    const old = document.getElementById("__kensho_banner");
    if (old) old.remove();
    const b = document.createElement("div");
    b.id = "__kensho_banner";
    b.style.cssText = "position:fixed;z-index:2147483647;right:12px;bottom:12px;max-width:340px;padding:12px 14px;" +
      "background:#111827;color:#fff;font:14px/1.5 sans-serif;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.3)";
    b.innerHTML = `<b>懸賞マネージャー</b><br>${RESULT.filled} 項目を入力しました` +
      (RESULT.unknown ? `<br><span style="color:#fde047">黄色の枠 ${RESULT.unknown} 項目は手で入力してください</span>` : "") +
      `<br>内容と応募規約を確認して、<b>送信は自分で</b>行ってください。` +
      `<div style="text-align:right;margin-top:4px"><a href="#" style="color:#93c5fd">閉じる</a></div>`;
    b.querySelector("a").onclick = (e) => { e.preventDefault(); b.remove(); };
    document.body.appendChild(b);
  }

  window.__KENSHO_RESULT = run();
})();
