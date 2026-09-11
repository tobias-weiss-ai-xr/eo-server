/**
 * opencloud-docserver i18n — internationalization for the editor UI.
 *
 * A dependency-free key→string translation module. Exposes three globals
 * (plus AMD/CommonJS exports for testability):
 *
 *   createI18n(config)      → t(key, defaultVal) translation function
 *   detectLocale()          → resolve navigator.language to a supported code
 *   applyTranslations(root) → localize [data-i18n] / [data-i18n-*] DOM nodes
 *
 * Usage:
 *   <script src="/static/i18n.js"></script>
 *   <script>
 *     const t = window.createI18n({ lng: window.detectLocale() });
 *     window.applyTranslations(document, t);  // static HTML gets translated
 *     status.textContent = t("Status.Ready"); // dynamic strings
 *   </script>
 */

"use strict";

(function (root, factory) {
  if (typeof define === "function" && define.amd) {
    define([], factory);
  } else if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    const api = factory();
    root.createI18n = api.createI18n;
    root.detectLocale = api.detectLocale;
    root.applyTranslations = api.applyTranslations;
  }
})(typeof self !== "undefined" ? self : this, function () {
  /**
   * Supported language codes (SSR locales; "pt-pt" and "zh-tw" are listed
   * separately because their base code resolves differently).
   */
  const SUPPORTED_LOCALES = [
    "ar",
    "bg",
    "ca",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "fi",
    "fr",
    "gl",
    "he",
    "hr",
    "hu",
    "hy",
    "id",
    "it",
    "ja",
    "ka",
    "kk",
    "ko",
    "lt",
    "lv",
    "mn",
    "ms",
    "nl",
    "pl",
    "pt",
    "pt-pt",
    "ro",
    "ru",
    "si",
    "sk",
    "sl",
    "sq",
    "sr",
    "sr-cyrl",
    "sv",
    "th",
    "tr",
    "uk",
    "ur",
    "vi",
    "zh",
    "zh-tw",
  ];

  /**
   * Default English translations — the fallback catalog. Every key in the
   * UI string catalog starts here; `t()` returns the key itself when even
   * this catalog has no entry (fail-safe, never a throw).
   */
  const DEFAULT_TRANSLATIONS = {
    "Tab.Home": "Home",
    "Tab.Insert": "Insert",
    "Tab.Draw": "Draw",
    "Tab.Layout": "Layout",
    "Tab.References": "References",
    "Tab.Collaboration": "Collaboration",
    "Tab.Protection": "Protection",
    "Tab.View": "View",
    "Tab.Plugins": "Plugins",
    "Tab.AI": "AI",
    "Tab.HeaderFooter": "Header & Footer",
    "HF.Close": "Close Header & Footer",
    "HF.PageNumber": "Page number",
    "HF.DateTime": "Date & time",
    "HF.DifferentFirst": "Different First Page",
    "HF.OddEven": "Different Odd & Even",
    "HF.HeaderFromTop": "Header from Top",
    "HF.FooterFromBottom": "Footer from Bottom",
    "Stub.NotImplemented": "not implemented yet",
    "AI.Propose.Applied": "AI proposal applied — review the tracked changes",
    "VersionHistory.Compare": "Compare",
    "VersionHistory.Comparing": "Comparing…",
    "VersionHistory.NoDiff": "No differences.",
    "Home.Highlight": "Highlight color",
    "Home.FontColor": "Font color",
    "Home.Shading": "Shading",
    "Home.Borders": "Borders",
    "Home.ChangeCase": "Change case",
    "Home.ChangeCase.Sentence": "Sentence case",
    "Home.ChangeCase.Lower": "lowercase",
    "Home.ChangeCase.Upper": "UPPERCASE",
    "Home.ChangeCase.Title": "Title Case",
    "Home.Multilevel": "Multilevel list",
    "Home.FontDec": "Decrease font",
    "Home.FontInc": "Increase font",
    "Home.MoreFormatting": "More formatting",
    "Insert.Shape": "Shape",
    "Insert.Chart": "Chart",
    "Insert.TextArt": "Text art",
    "Insert.DropCap": "Drop cap",
    "Insert.TextBox": "Text box",
    "Insert.Hyperlink": "Hyperlink",
    "Draw.InkThickness": "Ink thickness",
    "Draw.SelectTitle": "Select objects",
    "Draw.PenTitle": "Draw with pen",
    "Draw.HighlighterTitle": "Highlight text",
    "Draw.EraserTitle": "Erase ink",
    "Draw.InkTitle": "Ink color",
    "Draw.InkThicknessTitle": "Ink thickness",
    "Ref.Crossref": "Cross-reference",
    "Plugins.Ocr": "OCR",
    "Plugins.PhotoEditor": "Photo editor",
    "PhotoEditor.ChooseFile": "Image file",
    "PhotoEditor.Empty": "No image loaded",
    "PhotoEditor.Brightness": "Brightness",
    "PhotoEditor.Contrast": "Contrast",
    "PhotoEditor.Saturation": "Saturation",
    "PhotoEditor.PresetNone": "None",
    "PhotoEditor.PresetGrayscale": "Grayscale",
    "PhotoEditor.PresetSepia": "Sepia",
    "PhotoEditor.PresetInvert": "Invert",
    "PhotoEditor.PresetBlur": "Blur",
    "PhotoEditor.Reset": "Reset",
    "PhotoEditor.Close": "Close",
    "AI.Grammar": "Grammar",
    "AI.Assistant": "AI assistant",
    "HF.SameAsPrev": "Same as previous",
    "Draw.Select": "Select",
    "Draw.Pen": "Pen",
    "Draw.Highlighter": "Highlighter",
    "Draw.Eraser": "Eraser",
    "Draw.Ink": "Ink color",
    "Layout.PageSetup": "Page setup",
    "Layout.Hyphenation": "Hyphenation",
    "Layout.LineNumbers": "Line numbers",
    "Layout.Watermark": "Watermark",
    "Ref.Caption": "Caption",
    "Ref.Citation": "Citation",
    "Ref.Index": "Index",
    "Ref.UpdateToc": "Update TOC",
    "Collab.Chat": "Chat",
    "Collab.Compare": "Compare",
    "Collab.Display": "Display mode",
    "Collab.PrevChange": "Previous change",
    "Collab.NextChange": "Next change",
    "Prot.Password": "Protect with password",
    "Prot.Restrict": "Restrict editing",
    "Prot.Title": "Protect document",
    "Prot.StateNone": "Document is not protected.",
    "Prot.StateRestricted": "Editing is restricted (read only).",
    "Prot.StatePassword": "Password protection is enforced.",
    "Prot.RestrictLabel": "Restrict editing (read only)",
    "Prot.NewPassword": "New password",
    "Prot.CurrentPassword": "Current password",
    "Prot.RemovePassword": "Remove password",
    "Prot.Apply": "Apply",
    "Prot.Cancel": "Cancel",
    "Prot.Hint": "Setting a password enforces the editing restriction.",
    "Prot.StatusRestricted": "Editing restricted (read only).",
    "Prot.StatusUnrestricted": "Editing allowed.",
    "Prot.ErrorWrongPassword": "Invalid password — protection not changed.",
    "View.Mode": "Document view",
    "View.Ruler": "Ruler",
    "View.Gridlines": "Gridlines",
    "View.Navigation": "Navigation",
    "ChatPanel.Title": "Chat",
    "ChatPanel.Empty": "No messages yet",
    "Status.ChatOpen": "Chat open",
    "Status.ChatClosed": "Chat closed",
    "View.Fullscreen": "Fullscreen",
    "View.Theme": "Theme",
    "View.Fit": "Fit page",
    "Plugins.Browse": "Browse plugins",
    "Plugins.Manage": "Manage plugins",
    "AI.ChangesTab": "AI changes",
    "AI.Summarize": "Summarize",
    "AI.Translate": "Translate",
    "AI.Rewrite": "Rewrite",
    "Toolbar.Bold": "Bold",
    "Toolbar.Italic": "Italic",
    "Toolbar.Underline": "Underline",
    "Toolbar.UnderlineDouble": "Double underline",
    "Toolbar.StrikeDouble": "Double strikethrough",
    "Toolbar.BulletDisc": "Disc",
    "Toolbar.BulletCircle": "Circle",
    "Toolbar.BulletSquare": "Square",
    "Toolbar.NumberDecimal": "1. 2. 3.",
    "Toolbar.NumberLowerAlpha": "a. b. c.",
    "Toolbar.NumberLowerRoman": "i. ii. iii.",
    "Toolbar.NumberUpperAlpha": "A. B. C.",
    "Toolbar.CodeBlock": "Code block",
    "Toolbar.Subscript": "Subscript",
    "Toolbar.Superscript": "Superscript",
    "Toolbar.Heading3": "Heading 3",
    "Toolbar.Heading1": "Heading 1",
    "Toolbar.Heading2": "Heading 2",
    "Toolbar.Paragraph": "Paragraph",
    "Toolbar.BulletList": "Bullet list",
    "Toolbar.NumberedList": "Numbered list",
    "Toolbar.MultilevelTitle": "Multilevel list",
    "Toolbar.CollapseTitle": "Show fewer toolbar buttons",
    "Ctx.Cut": "Cut",
    "Ctx.Copy": "Copy",
    "Ctx.Paste": "Paste",
    "Ctx.PageBreakBefore": "Page break before",
    "Toolbar.FootnoteTitle": "Insert footnote",
    "Toolbar.EndnoteTitle": "Insert endnote",
    "Toolbar.PageNumberTitle": "Insert page number",
    "Toolbar.HeaderTitle": "Insert page header",
    "Toolbar.FooterTitle": "Insert page footer",
    "Ctx.AddComment": "Add comment",
    "Ctx.Link": "Link",
    "Find.ReplacePlaceholder": "Replace with",
    "User.EditingAs": "Editing as",
    "User.Document": "Document",
    "Toolbar.Multilevel": "Multilevel list",
    "Toolbar.InsertTable": "Insert table",
    "Toolbar.Undo": "Undo",
    "Toolbar.Redo": "Redo",
    "Toolbar.Save": "Save",
    "Toolbar.BoldTitle": "Bold (Ctrl+B)",
    "Toolbar.ItalicTitle": "Italic (Ctrl+I)",
    "Toolbar.UnderlineTitle": "Underline (Ctrl+U)",
    "Toolbar.Heading1Title": "Heading 1",
    "Toolbar.Heading2Title": "Heading 2",
    "Toolbar.ParagraphTitle": "Paragraph",
    "Toolbar.BulletListTitle": "Bullet list",
    "Toolbar.NumberedListTitle": "Numbered list",
    "Toolbar.InsertTableTitle": "Insert table",
    "Toolbar.SmallCapsTitle": "Small caps",
    "Toolbar.AllCapsTitle": "Uppercase",
    "Toolbar.AllCaps": "All caps",
    "Toolbar.DefaultFont": "Default",
    "Toolbar.ToggleDirection": "Toggle paragraph direction",
    "Toolbar.CodeTitle": "Inline code",
    "Toolbar.DirectionRtlTitle": "Right-to-left paragraph",
    "Toolbar.UndoTitle": "Undo (Ctrl+Z)",
    "Toolbar.RedoTitle": "Redo (Ctrl+Y)",
    "Toolbar.SaveTitle": "Save (Ctrl+S)",
    "Status.Ready": "Ready",
    "Status.Loading": "Loading…",
    "Status.Saving": "Saving…",
    "Status.Saved": "Saved ✓",
    "Status.Unsaved": "Unsaved changes…",
    "Status.LoadFailed": "Load failed: ",
    "Status.SaveFailed": "Save failed: ",
    "Status.OfflineQueued": "Offline — changes kept in this browser",
    "Status.OfflineIndicator": "Offline",
    "Status.Synced": "Back online — saved ✓",
    "Status.EmptyDocument": "Empty document — start typing",
    "Status.ReadOnly": "Read-only — another user is editing this document",
    "Status.NoSelection": "Select text first",
    "Table.Columns": "Columns",
    "Table.Rows": "Rows",
    "Prompt.TableColumns": "Number of columns:",
    "Prompt.TableRows": "Number of rows:",
  };

  /**
   * Built-in German catalog. Embedded so that a German browser gets a real
   * localized UI out of the box and `changeLanguage("de")` demonstrably
   * switches the interface. Additional languages can be shipped as static
   * JSON loaded via the `localePath` config option.
   */
  const LOCALE_DE = {
    "Tab.Draw": "Zeichnen",
    "Tab.References": "Verweise",
    "Tab.Collaboration": "Zusammenarbeit",
    "Tab.Protection": "Schutz",
    "Tab.View": "Ansicht",
    "Tab.Plugins": "Plugins",
    "Tab.AI": "KI",
    "Tab.HeaderFooter": "Kopf- und Fußzeile",
    "HF.Close": "Kopf- und Fußzeile schließen",
    "HF.PageNumber": "Seitenzahl",
    "HF.DateTime": "Datum und Uhrzeit",
    "HF.DifferentFirst": "Erste Seite anders",
    "HF.OddEven": "Unterschiedliche gerade/ungerade",
    "HF.HeaderFromTop": "Kopfzeile von oben",
    "HF.FooterFromBottom": "Fußzeile von unten",
    "Stub.NotImplemented": "noch nicht implementiert",
    "AI.Propose.Applied": "KI-Vorschlag angewendet — Änderungsnachverfolgung prüfen",
    "VersionHistory.Compare": "Vergleichen",
    "VersionHistory.Comparing": "Vergleiche…",
    "VersionHistory.NoDiff": "Keine Unterschiede.",
    "Home.Highlight": "Textmarkierung",
    "Home.FontColor": "Schriftfarbe",
    "Home.Shading": "Schattierung",
    "Home.Borders": "Rahmen",
    "Home.ChangeCase": "Groß-/Kleinschreibung",
    "Home.ChangeCase.Sentence": "Satzanfang",
    "Home.ChangeCase.Lower": "kleinschreibung",
    "Home.ChangeCase.Upper": "GROSSSCHREIBUNG",
    "Home.ChangeCase.Title": "Titelform",
    "Home.Multilevel": "Mehrstufige Liste",
    "Home.FontDec": "Schrift verkleinern",
    "Home.FontInc": "Schrift vergrößern",
    "Home.MoreFormatting": "Weitere Formatierung",
    "Insert.Shape": "Form",
    "Insert.Chart": "Diagramm",
    "Insert.TextArt": "Textkunst",
    "Insert.DropCap": "Initial",
    "Insert.TextBox": "Textfeld",
    "Insert.Hyperlink": "Hyperlink",
    "Draw.InkThickness": "Tintenstärke",
    "Draw.SelectTitle": "Objekte auswählen",
    "Draw.PenTitle": "Mit Stift zeichnen",
    "Draw.HighlighterTitle": "Text markieren",
    "Draw.EraserTitle": "Tinte radieren",
    "Draw.InkTitle": "Tintenfarbe",
    "Draw.InkThicknessTitle": "Tintenstärke",
    "Ref.Crossref": "Querverweis",
    "Plugins.Ocr": "OCR",
    "Plugins.PhotoEditor": "Fotoeditor",
    "PhotoEditor.ChooseFile": "Bilddatei",
    "PhotoEditor.Empty": "Kein Bild geladen",
    "PhotoEditor.Brightness": "Helligkeit",
    "PhotoEditor.Contrast": "Kontrast",
    "PhotoEditor.Saturation": "Sättigung",
    "PhotoEditor.PresetNone": "Keine",
    "PhotoEditor.PresetGrayscale": "Graustufen",
    "PhotoEditor.PresetSepia": "Sepia",
    "PhotoEditor.PresetInvert": "Invertieren",
    "PhotoEditor.PresetBlur": "Weichzeichnen",
    "PhotoEditor.Reset": "Zurücksetzen",
    "PhotoEditor.Close": "Schließen",
    "AI.Grammar": "Grammatik",
    "AI.Assistant": "KI-Assistent",
    "HF.SameAsPrev": "Wie vorherige",
    "Draw.Select": "Auswählen",
    "Draw.Pen": "Stift",
    "Draw.Highlighter": "Textmarker",
    "Draw.Eraser": "Radierer",
    "Draw.Ink": "Tintenfarbe",
    "Layout.PageSetup": "Seite einrichten",
    "Layout.Hyphenation": "Silbentrennung",
    "Layout.LineNumbers": "Zeilennummern",
    "Layout.Watermark": "Wasserzeichen",
    "Ref.Caption": "Beschriftung",
    "Ref.Citation": "Zitat",
    "Ref.Index": "Index",
    "Ref.UpdateToc": "Verzeichnis aktualisieren",
    "Collab.Chat": "Chat",
    "Collab.Compare": "Vergleichen",
    "Collab.Display": "Anzeigemodus",
    "Collab.PrevChange": "Vorherige Änderung",
    "Collab.NextChange": "Nächste Änderung",
    "Prot.Password": "Mit Kennwort schützen",
    "Prot.Restrict": "Bearbeitung einschränken",
    "Prot.Title": "Dokument schützen",
    "Prot.StateNone": "Dokument ist nicht geschützt.",
    "Prot.StateRestricted": "Bearbeitung ist eingeschränkt (schreibgeschützt).",
    "Prot.StatePassword": "Kennwortschutz ist aktiv.",
    "Prot.RestrictLabel": "Bearbeitung einschränken (schreibgeschützt)",
    "Prot.NewPassword": "Neues Kennwort",
    "Prot.CurrentPassword": "Aktuelles Kennwort",
    "Prot.RemovePassword": "Kennwort entfernen",
    "Prot.Apply": "Übernehmen",
    "Prot.Cancel": "Abbrechen",
    "Prot.Hint": "Ein Kennwort erzwingt die Bearbeitungseinschränkung.",
    "Prot.StatusRestricted": "Bearbeitung eingeschränkt (schreibgeschützt).",
    "Prot.StatusUnrestricted": "Bearbeitung erlaubt.",
    "Prot.ErrorWrongPassword": "Ungültiges Kennwort — Schutz nicht geändert.",
    "View.Mode": "Dokumentansicht",
    "View.Ruler": "Lineal",
    "View.Gridlines": "Gitternetzlinien",
    "View.Navigation": "Navigation",
    "ChatPanel.Title": "Chat",
    "ChatPanel.Empty": "Noch keine Nachrichten",
    "Status.ChatOpen": "Chat geöffnet",
    "Status.ChatClosed": "Chat geschlossen",
    "View.Fullscreen": "Vollbild",
    "View.Theme": "Design",
    "View.Fit": "Seite einpassen",
    "Plugins.Browse": "Plugins durchsuchen",
    "Plugins.Manage": "Plugins verwalten",
    "AI.ChangesTab": "KI-Änderungen",
    "AI.Summarize": "Zusammenfassen",
    "AI.Translate": "Übersetzen",
    "AI.Rewrite": "Neu formulieren",
    "Toolbar.Bold": "Fett",
    "Toolbar.Italic": "Kursiv",
    "Toolbar.Underline": "Unterstrichen",
    "Toolbar.UnderlineDouble": "Doppelt unterstrichen",
    "Toolbar.StrikeDouble": "Doppelt durchgestrichen",
    "Toolbar.BulletDisc": "Punkt",
    "Toolbar.BulletCircle": "Kreis",
    "Toolbar.BulletSquare": "Quadrat",
    "Toolbar.NumberDecimal": "1. 2. 3.",
    "Toolbar.NumberLowerAlpha": "a. b. c.",
    "Toolbar.NumberLowerRoman": "i. ii. iii.",
    "Toolbar.NumberUpperAlpha": "A. B. C.",
    "Toolbar.CodeBlock": "Code-Block",
    "Toolbar.Subscript": "Tiefgestellt",
    "Toolbar.Superscript": "Hochgestellt",
    "Toolbar.Heading3": "Überschrift 3",
    "Toolbar.Heading1": "Überschrift 1",
    "Toolbar.Heading2": "Überschrift 2",
    "Toolbar.Paragraph": "Absatz",
    "Toolbar.BulletList": "Aufzählungsliste",
    "Toolbar.NumberedList": "Nummerierte Liste",
    "Toolbar.MultilevelTitle": "Mehrebenenliste",
    "Toolbar.CollapseTitle": "Weniger Werkzeuge anzeigen",
    "Ctx.Cut": "Ausschneiden",
    "Ctx.Copy": "Kopieren",
    "Ctx.Paste": "Einfügen",
    "Ctx.PageBreakBefore": "Seitenumbruch davor",
    "Toolbar.FootnoteTitle": "Fußnote einfügen",
    "Toolbar.EndnoteTitle": "Endnote einfügen",
    "Toolbar.PageNumberTitle": "Seitenzahl einfügen",
    "Toolbar.HeaderTitle": "Kopfzeile einfügen",
    "Toolbar.FooterTitle": "Fußzeile einfügen",
    "Ctx.AddComment": "Kommentar hinzufügen",
    "Ctx.Link": "Link",
    "Find.ReplacePlaceholder": "Ersetzen durch",
    "User.EditingAs": "Bearbeiten als",
    "User.Document": "Dokument",
    "Toolbar.Multilevel": "Mehrebenenliste",
    "Toolbar.InsertTable": "Tabelle einfügen",
    "Toolbar.Undo": "Rückgängig",
    "Toolbar.Redo": "Wiederholen",
    "Toolbar.Save": "Speichern",
    "Toolbar.BoldTitle": "Fett (Strg+B)",
    "Toolbar.ItalicTitle": "Kursiv (Strg+I)",
    "Toolbar.UnderlineTitle": "Unterstrichen (Strg+U)",
    "Toolbar.Heading1Title": "Überschrift 1",
    "Toolbar.Heading2Title": "Überschrift 2",
    "Toolbar.ParagraphTitle": "Absatz",
    "Toolbar.BulletListTitle": "Aufzählungsliste",
    "Toolbar.NumberedListTitle": "Nummerierte Liste",
    "Toolbar.InsertTableTitle": "Tabelle einfügen",
    "Toolbar.SmallCapsTitle": "Kapitälchen",
    "Toolbar.AllCapsTitle": "Großbuchstaben",
    "Toolbar.AllCaps": "Großbuchstaben",
    "Toolbar.DefaultFont": "Standard",
    "Toolbar.ToggleDirection": "Absatzrichtung umschalten",
    "Toolbar.CodeTitle": "Inline-Code",
    "Toolbar.DirectionRtlTitle": "Rechts-nach-links-Absatz",
    "Toolbar.UndoTitle": "Rückgängig (Strg+Z)",
    "Toolbar.RedoTitle": "Wiederholen (Strg+Y)",
    "Toolbar.SaveTitle": "Speichern (Strg+S)",
    "Status.Ready": "Bereit",
    "Status.Loading": "Lädt…",
    "Status.Saving": "Speichert…",
    "Status.Saved": "Gespeichert ✓",
    "Status.Unsaved": "Ungespeicherte Änderungen…",
    "Status.LoadFailed": "Laden fehlgeschlagen: ",
    "Status.SaveFailed": "Speichern fehlgeschlagen: ",
    "Status.OfflineQueued": "Offline — Änderungen bleiben in diesem Browser",
    "Status.OfflineIndicator": "Offline",
    "Status.Synced": "Wieder online — gespeichert ✓",
    "Status.EmptyDocument": "Leeres Dokument — beginnen Sie mit der Eingabe",
    "Status.ReadOnly": "Schreibgeschützt — ein anderer Benutzer bearbeitet dieses Dokument",
    "Status.NoSelection": "Zuerst Text auswählen",
    "Table.Columns": "Spalten",
    "Table.Rows": "Zeilen",
    "Prompt.TableColumns": "Anzahl der Spalten:",
    "Prompt.TableRows": "Anzahl der Zeilen:",
    "Image.Width": "Width (px)",
    "Image.Height": "Height (px)",
    "Image.SizeHint": "leave empty for the original size",
  };

  /** Language code → embedded catalog (merged over the English default). */
  const LOCALE_CATALOGS = { de: LOCALE_DE };

  /**
   * Full fallback catalog for a language code: English defaults overlaid
   * with the embedded catalog for that language, if any.
   */
  function catalogFor(code) {
    return Object.assign({}, DEFAULT_TRANSLATIONS, LOCALE_CATALOGS[code] || {});
  }

  /**
   * Resolve a user-facing language tag ("de-DE", "pt-PT", "zh-CN", …) to the
   * closest supported locale code, falling back to English ("en"). Tags that
   * are not supported as a whole degrade to their base code ("de-DE" → "de").
   */
  function detectLocale(navigatorLike) {
    const nav = navigatorLike || (typeof navigator !== "undefined" ? navigator : null);
    const candidates = [];
    if (nav && Array.isArray(nav.languages)) candidates.push.apply(candidates, nav.languages);
    if (nav && nav.language) candidates.push(nav.language);
    candidates.push("en"); // last resort
    for (let i = 0; i < candidates.length; i += 1) {
      const code = String(candidates[i]).toLowerCase().replace("_", "-");
      if (SUPPORTED_LOCALES.indexOf(code) !== -1) return code;
      const base = code.split("-")[0];
      if (SUPPORTED_LOCALES.indexOf(base) !== -1) return base;
    }
    return "en";
  }

  /** data-i18n-<attribute> markers that applyTranslations understands. */
  const I18N_ATTRS = ["title", "placeholder", "aria-label"];

  /**
   * Localize every element carrying a data-i18n marker within root:
   *
   *   data-i18n             → element.textContent
   *   data-i18n-title       → element title attribute
   *   data-i18n-placeholder → element placeholder attribute
   *   data-i18n-aria-label  → element aria-label attribute
   *
   * Elements without markers are left untouched, so it is safe to call on
   * the whole document. Returns false when the DOM is unavailable (e.g. in
   * a Node.js unit test) so callers can skip gracefully.
   */
  function applyTranslations(rootEl, t) {
    const root = rootEl || (typeof document !== "undefined" ? document : null);
    if (!root || typeof root.querySelectorAll !== "function") return false;
    root.querySelectorAll("[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key);
    });
    I18N_ATTRS.forEach(function (attr) {
      const marker = "data-i18n-" + attr;
      root.querySelectorAll("[" + marker + "]").forEach(function (el) {
        const key = el.getAttribute(marker);
        if (key) el.setAttribute(attr, t(key));
      });
    });
    return true;
  }

  /**
   * Create an i18n instance with the given configuration.
   *
   * @param {Object} config — Configuration object
   * @param {string} [config.lng="en"] — Language code (e.g. "en", "de", "fr")
   * @param {Object} [config.translations] — Custom translations merged over the catalog
   * @param {string} [config.localePath] — Base URL for loading locale JSON files
   * @param {Function} [config.onLoaded] — Called after a remote locale file merges in
   * @returns {Function} t(key, defaultVal) plus t.lng / t.changeLanguage / t.loadTranslations
   */
  function createI18n(config = {}) {
    const { lng = "en", translations = {}, localePath, onLoaded } = config;
    const resources = {};

    function resolve(code) {
      return SUPPORTED_LOCALES.indexOf(code) !== -1 ? code : "en";
    }

    function t(key, defaultVal) {
      const bucket = resources[t.lng];
      const value =
        bucket && bucket.translation && bucket.translation[key] !== undefined
          ? bucket.translation[key]
          : undefined;
      return value !== undefined ? value : defaultVal !== undefined ? defaultVal : key;
    }

    t.lng = resolve(lng);
    resources[t.lng] = { translation: Object.assign(catalogFor(t.lng), translations) };
    t.resources = resources;
    t.supportedLocales = SUPPORTED_LOCALES;

    /**
     * Load (and merge) a locale JSON file, e.g. `${localePath}/de.json`.
     * Only meaningful when config.localePath was provided.
     */
    t.loadTranslations = function (code) {
      const target = code || t.lng;
      const url = localePath + "/" + target + ".json";
      fetch(url)
        .then(function (response) {
          if (!response.ok) throw new Error("Failed to load locale: " + response.statusText);
          return response.json();
        })
        .then(function (localeData) {
          if (localeData && typeof localeData === "object") {
            if (!resources[target]) resources[target] = { translation: catalogFor(target) };
            resources[target].translation = Object.assign({}, resources[target].translation, localeData);
            if (target === t.lng && typeof onLoaded === "function") onLoaded();
          }
        })
        .catch(function (err) {
          console.warn("[i18n] Could not load locale from " + url + ": " + err.message);
        });
      return undefined;
    };

    /**
     * Switch the active language at runtime. Embedded catalogs apply
     * immediately; remote JSON catalogs load asynchronously.
     */
    t.changeLanguage = function (code) {
      const next = resolve(code);
      if (!resources[next]) resources[next] = { translation: catalogFor(next) };
      t.lng = next;
      if (localePath && next !== "en") t.loadTranslations(next);
      return next;
    };

    // Pre-fetch the remote catalog for the initial language, if configured.
    if (localePath && t.lng !== "en") t.loadTranslations(t.lng);

    return t;
  }

  return {
    createI18n: createI18n,
    detectLocale: detectLocale,
    applyTranslations: applyTranslations,
    DEFAULT_TRANSLATIONS: DEFAULT_TRANSLATIONS,
    LOCALE_CATALOGS: LOCALE_CATALOGS,
    SUPPORTED_LOCALES: SUPPORTED_LOCALES,
  };
});
