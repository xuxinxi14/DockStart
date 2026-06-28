import Notice from "./Notice";

type ScientificNoticeProps = {
  children?: string;
  title?: string;
};

const defaultMessage = "Docking score 仅供结构结合趋势参考，不能替代实验验证。";

export default function ScientificNotice({ children = defaultMessage, title = "科学边界" }: ScientificNoticeProps) {
  return (
    <Notice title={title} tone="scientific">
      {children}
    </Notice>
  );
}
