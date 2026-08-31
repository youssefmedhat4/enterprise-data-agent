"use client";

import { ChevronDown, Database } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  dataSourceName,
  type DataSourceSummary,
} from "@/lib/datasources/datasources";
import { cn } from "@/lib/utils";

interface DataSourceSelectorProps {
  value: string;
  sources: readonly DataSourceSummary[];
  onValueChange: (dataSourceId: string) => void;
  disabled: boolean;
  compact?: boolean;
}

/**
 * Which database the workspace is querying.
 *
 * Shows the name only. Connection references and anything resembling a
 * credential stay on the backend and never reach this component's props.
 */
export function DataSourceSelector({
  value,
  sources,
  onValueChange,
  disabled,
  compact = false,
}: DataSourceSelectorProps) {
  const label = dataSourceName(sources, value);
  const onlyOne = sources.length <= 1;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled || onlyOne}
          aria-label={`Data source: ${label}`}
          className={cn(
            "inline-flex h-7 min-w-0 items-center gap-1.5 rounded-md px-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground data-[state=open]:bg-muted data-[state=open]:text-foreground disabled:cursor-not-allowed disabled:opacity-50",
            compact ? "max-w-[12rem]" : "max-w-[14rem]",
          )}
        >
          <Database className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="truncate">{label}</span>
          {onlyOne ? null : (
            <ChevronDown className="size-3 shrink-0" aria-hidden="true" />
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" sideOffset={6} className="min-w-56">
        <DropdownMenuLabel>Data source</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={value} onValueChange={onValueChange}>
          {sources.map((source) => (
            <DropdownMenuRadioItem key={source.id} value={source.id}>
              <span className="truncate">{source.name}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
