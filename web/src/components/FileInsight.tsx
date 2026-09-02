import type { LocalizedText } from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { cx } from "./ui";

/** One measured, row-free sentence shared by Intake and Project Data. */
export function FileInsight({
  insight,
  className,
}: {
  insight?: LocalizedText;
  className?: string;
}) {
  if (!insight) return null;
  const text = insight[activeLanguage()] || insight.en;
  return (
    <p className={cx("mt-2 min-w-0 text-[10px] leading-relaxed text-ink-mute", className)} title={text}>
      {/* #382: the label is bold and brand-coloured, which on its own read
          as a button -- it is a plain <p> with no handler. The colon makes it
          a label introducing the sentence beside it. In the JSX rather than
          the translated string, so it holds for both languages. */}
      <span className="mr-1 font-semibold text-brand-700">✦ {t("File insight")}:</span>
      <span>{text}</span>
    </p>
  );
}
