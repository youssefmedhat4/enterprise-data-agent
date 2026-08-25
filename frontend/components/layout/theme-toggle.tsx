"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const OPTIONS = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
] as const;

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // The resolved theme is unknown during SSR; render a stable placeholder so the
  // icon does not flip after hydration.
  useEffect(() => setMounted(true), []);

  const active = OPTIONS.find((option) => option.value === theme) ?? OPTIONS[2];
  const Icon = mounted ? active.Icon : Monitor;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 text-muted-foreground hover:text-foreground"
        >
          <Icon className="size-4" aria-hidden="true" />
          <span className="sr-only">
            {mounted ? `Theme: ${active.label}. Change theme` : "Change theme"}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        {OPTIONS.map(({ value, label, Icon: OptionIcon }) => (
          <DropdownMenuItem
            key={value}
            onSelect={() => setTheme(value)}
            className="gap-2 text-[13px]"
          >
            <OptionIcon className="size-4" aria-hidden="true" />
            {label}
            {mounted && theme === value ? (
              <span className="ms-auto text-xs text-muted-foreground">Active</span>
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
