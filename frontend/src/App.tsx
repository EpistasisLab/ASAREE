import { Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { LoginPage } from '@/pages/LoginPage'
import { RegisterPage } from '@/pages/RegisterPage'
import { ProfilePage } from '@/pages/ProfilePage'
import { ExperimentsPage } from '@/pages/ExperimentsPage'
import { ExperimentDetailPage } from '@/pages/ExperimentDetailPage'

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/experiments" element={<ExperimentsPage />} />
        <Route path="/experiments/:experimentId" element={<ExperimentDetailPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/experiments" replace />} />
      <Route path="*" element={<Navigate to="/experiments" replace />} />
    </Routes>
  )
}
