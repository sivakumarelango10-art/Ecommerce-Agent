import React, { useState, useEffect } from 'react';
import { AdminGuard } from './AdminGuard';
import { AdminLayout } from './AdminLayout';
import { AdminOverview } from './AdminOverview';
import { UserManagement } from './UserManagement';
import { AdminAnalytics } from './AdminAnalytics';
import { AdminAuditLogs } from './AdminAuditLogs';
import { AdminManagement } from './AdminManagement';
import { AIProviderCatalogue } from './AIProviderCatalogue';
import { PlatformKeyVault } from './PlatformKeyVault';
import { AIModelRegistry } from './AIModelRegistry';
import { AIRoutingPreferences } from './AIRoutingPreferences';
import { AIProviderActivity } from './AIProviderActivity';

export interface AdminDashboardProps {
  onBackToStore?: () => void;
  initialTab?: string;
}

export const AdminDashboard: React.FC<AdminDashboardProps> = ({
  onBackToStore,
  initialTab = 'overview',
}) => {
  const [activeTab, setActiveTab] = useState<string>(initialTab);

  // Sync tab with URL hash if present
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const hash = window.location.hash.replace('#', '');
      if ([
        'overview',
        'users',
        'analytics',
        'providers',
        'api-keys',
        'models',
        'routing',
        'provider-activity',
        'audit-logs',
        'admins',
      ].includes(hash)) {
        setActiveTab(hash);
      }
    }
  }, []);

  const handleTabChange = (tabId: string) => {
    setActiveTab(tabId);
    if (typeof window !== 'undefined') {
      window.location.hash = tabId;
    }
  };

  const renderActiveView = () => {
    switch (activeTab) {
      case 'overview':
        return <AdminOverview onNavigate={(tab: string) => handleTabChange(tab)} />;
      case 'users':
        return <UserManagement />;
      case 'analytics':
        return <AdminAnalytics />;
      case 'providers':
        return <AIProviderCatalogue />;
      case 'api-keys':
        return <PlatformKeyVault />;
      case 'models':
        return <AIModelRegistry />;
      case 'routing':
        return <AIRoutingPreferences />;
      case 'provider-activity':
        return <AIProviderActivity />;
      case 'audit-logs':
        return <AdminAuditLogs />;
      case 'admins':
        return <AdminManagement />;
      default:
        return <AdminOverview onNavigate={(tab: string) => handleTabChange(tab)} />;
    }
  };

  return (
    <AdminGuard onBackToStore={onBackToStore}>
      <AdminLayout
        activeTab={activeTab}
        onTabChange={handleTabChange}
        onBackToStore={onBackToStore}
      >
        {renderActiveView()}
      </AdminLayout>
    </AdminGuard>
  );
};

export default AdminDashboard;
