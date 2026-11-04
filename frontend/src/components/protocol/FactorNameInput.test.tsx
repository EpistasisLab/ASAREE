import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { FactorNameInput } from './FactorNameInput'

afterEach(cleanup)

it('blocks a typed colon without renaming a legacy factor', async () => {
  const user = userEvent.setup()
  const change = vi.fn()
  render(<FactorNameInput id="factor-name" value="Writer:Prompt" onValueChange={change} />)
  const input = screen.getByRole('textbox')
  expect(input).toHaveValue('Writer › Prompt')
  await user.type(input, ':')
  expect(input).toHaveValue('Writer › Prompt')
  expect(change).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('Colons (:) aren’t allowed')
})

it('rejects pasted names containing a colon', async () => {
  const user = userEvent.setup()
  const change = vi.fn()
  render(<FactorNameInput id="factor-name" value="" onValueChange={change} />)
  const input = screen.getByRole('textbox')
  await user.click(input)
  await user.paste('Custom:Factor')
  expect(input).toHaveValue('')
  expect(change).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('Use ›')
})

it('accepts a name with the new separator after rejecting a colon', async () => {
  const user = userEvent.setup()
  const change = vi.fn()
  render(<FactorNameInput id="factor-name" value="" onValueChange={change} />)
  await user.click(screen.getByRole('textbox'))
  await user.paste('Custom:Factor')
  await user.paste('Custom › Factor')
  expect(change).toHaveBeenCalledExactlyOnceWith('Custom › Factor')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
