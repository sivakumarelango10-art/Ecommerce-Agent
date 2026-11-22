import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatMessageItem } from '../components/chat/ChatMessageItem';
import { ChatMessage } from '../types/api';

describe('ChatMessageItem Component', () => {
  it('renders a user message aligned to the right', () => {
    const userMsg: ChatMessage = {
      id: '1',
      sender: 'user',
      timestamp: '10:30 AM',
      text: 'Where is my order ORD-20261122-001?',
    };

    render(<ChatMessageItem message={userMsg} />);
    expect(screen.getByText('Where is my order ORD-20261122-001?')).toBeInTheDocument();
  });

  it('renders order_support destination badge and clickable order number chip', () => {
    const onInspect = vi.fn();
    const botMsg: ChatMessage = {
      id: '2',
      sender: 'assistant',
      timestamp: '10:31 AM',
      destination: 'order_support',
      orderAnswer: 'Your order ORD-20261122-001 is currently shipped and on its way.',
    };

    render(<ChatMessageItem message={botMsg} onInspectOrderNumber={onInspect} />);

    expect(screen.getByText(/Order Support/i)).toBeInTheDocument();
    expect(screen.getByText(/Your order ORD-20261122-001 is currently shipped/i)).toBeInTheDocument();

    // Inspect button chip
    const inspectBtn = screen.getByRole('button', { name: /ORD-20261122-001/i });
    expect(inspectBtn).toBeInTheDocument();
    fireEvent.click(inspectBtn);
    expect(onInspect).toHaveBeenCalledWith('ORD-20261122-001');
  });

  it('renders shopping_agent destination with product cards from products array', () => {
    const botMsg: ChatMessage = {
      id: '3',
      sender: 'assistant',
      timestamp: '10:32 AM',
      destination: 'shopping_agent',
      shoppingResponse: {
        message: 'Here are the top wireless headphones:',
        products: [
          {
            id: 'prod-uuid-101',
            sku: 'SKU-AUDIO-01',
            name: 'Noise Cancelling Headphones',
            price: 199.99,
            currency: 'USD',
            categories: [{ name: 'Audio' }],
            in_stock: true,
            recommendation_reason: 'Best in class battery life and sound quality.',
          },
        ],
      },
    };

    render(<ChatMessageItem message={botMsg} />);

    expect(screen.getByText(/Shopping Assistant/i)).toBeInTheDocument();
    expect(screen.getByText('Here are the top wireless headphones:')).toBeInTheDocument();
    expect(screen.getByText('Noise Cancelling Headphones')).toBeInTheDocument();
    expect(screen.getByText('$199.99')).toBeInTheDocument();
    expect(screen.getByText('● In Stock')).toBeInTheDocument();
    expect(screen.getByText(/"Best in class battery life and sound quality."/i)).toBeInTheDocument();
  });

  it('renders general_assistant destination', () => {
    const botMsg: ChatMessage = {
      id: '4',
      sender: 'assistant',
      timestamp: '10:33 AM',
      destination: 'general_assistant',
      generalAnswer: 'Our standard return policy is 30 days with receipt.',
    };

    render(<ChatMessageItem message={botMsg} />);

    expect(screen.getByText(/General Support/i)).toBeInTheDocument();
    expect(screen.getByText('Our standard return policy is 30 days with receipt.')).toBeInTheDocument();
  });
});
