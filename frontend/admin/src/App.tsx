import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Empty } from "@shared/ui";
import { AuthProvider, useMe } from "./auth";
import { Layout } from "./Layout";
import { AntifraudPage } from "./pages/Antifraud";
import { AuditPage } from "./pages/Audit";
import { DashboardPage } from "./pages/Dashboard";
import { OrderDetailPage } from "./pages/OrderDetail";
import { OrdersPage } from "./pages/Orders";
import { ProfilePage } from "./pages/Profile";
import { SettingsPage } from "./pages/Settings";
import { StaffPage } from "./pages/Staff";
import { UserDetailPage } from "./pages/UserDetail";
import { UsersPage } from "./pages/Users";
import { WithdrawalDetailPage } from "./pages/WithdrawalDetail";
import { WithdrawalsPage } from "./pages/Withdrawals";

function AdminOnly({ children }: { children: ReactNode }) {
  const me = useMe();
  if (!me.is_admin) return <Empty text="Раздел доступен только администратору." />;
  return <>{children}</>;
}

export function App() {
  return (
    <BrowserRouter basename="/admin">
      <AuthProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Navigate to="/withdrawals" replace />} />
            <Route path="withdrawals" element={<WithdrawalsPage />} />
            <Route path="withdrawals/:id" element={<WithdrawalDetailPage />} />
            <Route path="orders" element={<OrdersPage />} />
            <Route path="orders/:id" element={<OrderDetailPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="users/:id" element={<UserDetailPage />} />
            <Route path="antifraud" element={<AntifraudPage />} />
            <Route path="dashboard" element={<AdminOnly><DashboardPage /></AdminOnly>} />
            <Route path="settings" element={<AdminOnly><SettingsPage /></AdminOnly>} />
            <Route path="staff" element={<AdminOnly><StaffPage /></AdminOnly>} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="profile" element={<ProfilePage />} />
            <Route path="*" element={<Empty text="Страница не найдена." />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
