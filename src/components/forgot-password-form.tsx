"use client"

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { requestPasswordReset } from "@/app/(auth)/actions"

const schema = z.object({
  email: z.string().email("Enter a valid email address"),
})

type FormValues = z.infer<typeof schema>

export function ForgotPasswordForm() {
  const [serverError, setServerError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "" },
  })

  async function onSubmit(values: FormValues) {
    setServerError(null)
    const result = await requestPasswordReset({ email: values.email })
    if (result?.error) {
      setServerError(result.error)
      return
    }
    setSubmitted(true)
  }

  if (submitted) {
    return (
      <div className="rounded-md bg-muted px-4 py-3 text-sm text-muted-foreground">
        If an account exists for that address, a password reset link is on its
        way. Check your inbox (and spam folder).
      </div>
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        {serverError && (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {serverError}
          </div>
        )}
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input
                  type="email"
                  placeholder="you@example.com"
                  autoComplete="email"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button
          type="submit"
          className="w-full"
          disabled={form.formState.isSubmitting}
        >
          {form.formState.isSubmitting ? "Sending…" : "Send reset link"}
        </Button>
      </form>
    </Form>
  )
}
