import { useEffect, useState, type ReactNode } from "react";

type DelayedPendingProps = {
  children: ReactNode;
  delay?: number;
};

export default function DelayedPending({ children, delay = 230 }: DelayedPendingProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), delay);
    return () => window.clearTimeout(timer);
  }, [delay]);

  return visible ? children : null;
}
