"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ErrorNotice, Loading } from "@/components/Async";
import { completeLogin } from "@/lib/auth";

function Callback() {
  const params = useSearchParams();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = params.get("code");
    const denied = params.get("error");
    if (denied) {
      setError(params.get("error_description") ?? denied);
      return;
    }
    if (!code) {
      setError("no authorization code in the callback");
      return;
    }
    completeLogin(code)
      .then(() => router.replace("/admin/"))
      .catch((err: Error) => setError(err.message));
  }, [params, router]);

  if (error) return <ErrorNotice message={error} />;
  return <Loading />;
}

export default function CallbackPage() {
  return (
    <main>
      <h1>Signing in…</h1>
      <Suspense fallback={<Loading />}>
        <Callback />
      </Suspense>
    </main>
  );
}
