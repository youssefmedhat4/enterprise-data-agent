"use client";

import { Bot, ChevronDown } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  MODEL_PROFILES,
  modelDisplayName,
  type ModelProfile,
} from "@/lib/models/profiles";
import { cn } from "@/lib/utils";

interface ModelSelectorProps {
  value: ModelProfile;
  onValueChange: (profile: ModelProfile) => void;
  disabled: boolean;
  compact?: boolean;
}

export function ModelSelector({
  value,
  onValueChange,
  disabled,
  compact = false,
}: ModelSelectorProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={`Model: ${modelDisplayName(value)}`}
          className={cn(
            "inline-flex h-7 min-w-0 items-center gap-1.5 rounded-md px-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground data-[state=open]:bg-muted data-[state=open]:text-foreground disabled:cursor-not-allowed disabled:opacity-50",
            compact ? "max-w-[12rem]" : "max-w-[14rem]",
          )}
        >
          <Bot className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="truncate">{modelDisplayName(value)}</span>
          <ChevronDown className="size-3 shrink-0" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" sideOffset={6} className="min-w-52">
        <DropdownMenuLabel>Analysis model</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => onValueChange(next as ModelProfile)}
        >
          {MODEL_PROFILES.map((profile) => (
            <DropdownMenuRadioItem key={profile.value} value={profile.value}>
              {profile.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
