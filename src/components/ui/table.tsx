import * as React from "react"

import { cn } from "@/lib/utils"

// Premium data table — the core surface of the product.
//
//   Rows        56px min height, generous padding, subtle hover tint.
//   Headers     11px uppercase, wide tracking, muted; sticky-capable.
//   Numerics    use <TableCell numeric> → mono + tabular-nums, right-aligned.
//   Borders     hairline row separators only. No vertical rules, no zebra.
//
//   <Table>
//     <TableHeader>
//       <TableRow><TableHead>Ticker</TableHead><TableHead numeric>Score</TableHead></TableRow>
//     </TableHeader>
//     <TableBody>
//       <TableRow interactive>…</TableRow>
//     </TableBody>
//   </Table>

function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table
        data-slot="table"
        className={cn("w-full caption-bottom border-collapse text-small", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b [&_tr]:border-border", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn("border-t border-border bg-secondary/40 font-medium", className)}
      {...props}
    />
  )
}

function TableRow({
  className,
  interactive = false,
  ...props
}: React.ComponentProps<"tr"> & {
  /** Adds the hover tint + pointer for click-through rows. */
  interactive?: boolean
}) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b border-border transition-premium",
        interactive && "cursor-pointer hover:bg-secondary/50",
        className,
      )}
      {...props}
    />
  )
}

function TableHead({
  className,
  numeric = false,
  ...props
}: React.ComponentProps<"th"> & { numeric?: boolean }) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-11 px-4 text-left align-middle text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground",
        numeric && "text-right",
        className,
      )}
      {...props}
    />
  )
}

function TableCell({
  className,
  numeric = false,
  ...props
}: React.ComponentProps<"td"> & {
  /** Mono + tabular numerals, right-aligned. For every number. */
  numeric?: boolean
}) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "h-14 px-4 align-middle",
        numeric && "text-data text-right",
        className,
      )}
      {...props}
    />
  )
}

function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-caption", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
