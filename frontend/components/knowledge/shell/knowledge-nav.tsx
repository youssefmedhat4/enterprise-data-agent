"use client";

import { motion } from "motion/react";
import { Tabs as TabsPrimitive } from "radix-ui";
import { ChevronDown, type LucideIcon } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * Navigation for the Knowledge workspace.
 *
 * Ten sections is too many for a row of tabs — the old bar wrapped onto two
 * lines and lost any sense of order. Down the side they fit in one column, and
 * grouping them says what the areas are for: what the database *is*, what the
 * system has *learned*, and whether any of it can be *trusted*.
 *
 * Built on the tabs primitive rather than links so that the sections keep their
 * roving-focus keyboard behaviour (arrow keys move between them, Home/End jump
 * to the ends) and so the panel each one controls is announced properly. The
 * styling is local because this is a sidebar, not a tab strip.
 */

export interface KnowledgeSection {
  value: string;
  label: string;
  icon: LucideIcon;
  /** Heading this section sits under. Empty means it stands alone on top. */
  group: string;
  /** Shown as a trailing count. Omitted rather than shown as zero. */
  count?: number;
  /** A count that is a queue someone has to work, so it earns colour. */
  attention?: boolean;
}

function Count({ value, attention }: { value: number; attention: boolean }) {
  return (
    <span
      className={cn(
        "ms-auto rounded-full px-1.5 py-px text-[11px] font-medium tabular-nums",
        "transition-colors duration-200",
        attention
          ? "bg-warning/15 text-warning"
          : "bg-muted text-muted-foreground",
      )}
    >
      {value}
    </span>
  );
}

export function KnowledgeNav({
  sections,
  value,
  onValueChange,
}: {
  sections: readonly KnowledgeSection[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const groups = sections.reduce<Array<[string, KnowledgeSection[]]>>(
    (accumulated, section) => {
      const last = accumulated.at(-1);
      if (last !== undefined && last[0] === section.group) {
        last[1].push(section);
        return accumulated;
      }
      accumulated.push([section.group, [section]]);
      return accumulated;
    },
    [],
  );

  return (
    <>
      {/* Below laptop width the column would crowd the content it navigates,
          so the same sections collapse into one menu. */}
      <div className="lg:hidden">
        <SectionMenu
          sections={sections}
          value={value}
          onValueChange={onValueChange}
        />
      </div>

      <TabsPrimitive.List
        aria-label="Knowledge sections"
        aria-orientation="vertical"
        className="sticky top-[88px] hidden w-[228px] shrink-0 flex-col self-start lg:flex"
      >
        {groups.map(([group, items], groupIndex) => (
          <div
            key={group === "" ? "primary" : group}
            className={cn(groupIndex > 0 && "mt-6")}
          >
            {group !== "" ? (
              /* Hidden from assistive technology on purpose: a tablist should
                 own only tabs, and every label below is already unambiguous
                 on its own. */
              <p
                aria-hidden="true"
                className="label-xs px-2.5 pb-2 text-muted-foreground/70"
              >
                {group}
              </p>
            ) : null}
            <div className="flex flex-col gap-px">
              {items.map((section) => {
                const Icon = section.icon;
                const active = section.value === value;
                return (
                  <TabsPrimitive.Trigger
                    key={section.value}
                    value={section.value}
                    className={cn(
                      "group/nav relative flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5",
                      "text-[13px] outline-none transition-colors duration-200",
                      "focus-visible:ring-[3px] focus-visible:ring-ring/50",
                      active
                        ? "bg-surface-raised font-medium text-foreground"
                        : "text-muted-foreground hover:bg-surface hover:text-foreground",
                    )}
                  >
                    {active ? (
                      <motion.span
                        layoutId="knowledge-section-active"
                        transition={SPRING}
                        aria-hidden="true"
                        className="absolute inset-y-2 start-0 w-[2px] rounded-full bg-primary"
                      />
                    ) : null}
                    <Icon
                      className={cn(
                        "size-4 shrink-0 transition-colors duration-200",
                        active ? "text-primary" : "text-muted-foreground/80",
                      )}
                      aria-hidden="true"
                    />
                    <span className="truncate">{section.label}</span>
                    {section.count !== undefined && section.count > 0 ? (
                      <Count
                        value={section.count}
                        attention={section.attention === true}
                      />
                    ) : null}
                  </TabsPrimitive.Trigger>
                );
              })}
            </div>
          </div>
        ))}
      </TabsPrimitive.List>
    </>
  );
}

/**
 * The narrow-width equivalent.
 *
 * Menu items rather than tabs, so the tablist above stays the single set of
 * tabs on the page however the layout is resized.
 */
function SectionMenu({
  sections,
  value,
  onValueChange,
}: {
  sections: readonly KnowledgeSection[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const current = sections.find((section) => section.value === value);
  const CurrentIcon = current?.icon;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex h-10 w-full items-center gap-2.5 rounded-lg border border-hairline bg-surface px-3",
          "text-[13px] font-medium outline-none transition-colors",
          "hover:bg-surface-raised focus-visible:ring-[3px] focus-visible:ring-ring/50",
        )}
      >
        {CurrentIcon !== undefined ? (
          <CurrentIcon className="size-4 text-primary" aria-hidden="true" />
        ) : null}
        <span className="truncate">{current?.label ?? "Section"}</span>
        <ChevronDown
          className="ms-auto size-4 text-muted-foreground"
          aria-hidden="true"
        />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-56">
        {sections.map((section, index) => {
          const Icon = section.icon;
          const previous = sections[index - 1];
          const startsGroup =
            section.group !== "" && previous?.group !== section.group;
          return (
            <div key={section.value}>
              {startsGroup ? (
                <DropdownMenuLabel className="label-xs text-muted-foreground/70">
                  {section.group}
                </DropdownMenuLabel>
              ) : null}
              <DropdownMenuItem
                onSelect={() => onValueChange(section.value)}
                className="gap-2.5"
              >
                <Icon className="size-4" aria-hidden="true" />
                {section.label}
                {section.count !== undefined && section.count > 0 ? (
                  <Count
                    value={section.count}
                    attention={section.attention === true}
                  />
                ) : null}
              </DropdownMenuItem>
            </div>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
