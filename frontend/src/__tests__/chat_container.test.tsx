import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatContainer } from '../components/chat/ChatContainer';
import { AuthProvider } from '../context/AuthContext';

describe('ChatContainer Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders welcome screen when empty with quick prompts', () => {
    render(
      <AuthProvider>
        <ChatContainer
          onInspectOrderNumber={vi.fn()}
          onOpenAuth={vi.fn()}
          onOpenGuestPortal={vi.fn()}
        />
      </AuthProvider>
    );

    expect(screen.getByText(/How can I assist your shopping today\?/i)).toBeInTheDocument();
    expect(screen.getByText('What are my recent orders?')).toBeInTheDocument();
    expect(screen.getByText('Recommend popular electronics')).toBeInTheDocument();
  });

  it('guides unauthenticated users asking order questions to sign in or guest portal', async () => {
    const onOpenAuth = vi.fn();
    const onOpenGuestPortal = vi.fn();

    render(
      <AuthProvider>
        <ChatContainer
          onInspectOrderNumber={vi.fn()}
          onOpenAuth={onOpenAuth}
          onOpenGuestPortal={onOpenGuestPortal}
        />
      </AuthProvider>
    );

    const input = screen.getByPlaceholderText(/Ask a question or sign in to track personal orders/i);
    fireEvent.change(input, { target: { value: 'Where is my order?' } });
    fireEvent.click(screen.getByRole('button', { name: /Send message/i }));

    // Should show guidance message without calling api.chat.sendQuestion
    expect(screen.getByText('Where is my order?')).toBeInTheDocument();
    expect(
      screen.getByText(/To access personal order details, please sign in to your account/i)
    ).toBeInTheDocument();
  });
});
