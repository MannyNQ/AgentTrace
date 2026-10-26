import { Nav } from "@/app/account/nav";
import { ChangePasswordForm } from "../forms";

export default function PasswordPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto w-full max-w-sm px-4 py-10 sm:px-6">
        <h1 className="text-2xl font-semibold tracking-tight">Change password</h1>
        <ChangePasswordForm />
      </main>
    </>
  );
}
