import abc
from typing import Any, Sequence


class Manager(metaclass=abc.ABCMeta):
    """A configzen configuration manager protocol."""

    @abc.abstractmethod
    def load(self, *args, **kwargs) -> 'Configuration':
        """Load the configuration."""
        pass

    @abc.abstractmethod
    def save(self, *args, **kwargs) -> None:
        """Save the configuration."""
        pass

    @abc.abstractmethod
    def items(self, *args, **kwargs) -> Sequence['Configured']:
        """Get the configuration items."""
        pass


class Configured(metaclass=abc.ABCMeta):
    """A configzen configuration item protocol."""

    manager: Manager

    @abc.abstractmethod
    def get(self, *args, **kwargs) -> Any:
        """Get the value of the configuration item."""
        pass

    @abc.abstractmethod
    def set(self, *args, **kwargs) -> None:
        """Set the value of the configuration item."""
        pass

    @abc.abstractmethod
    def delete(self, *args, **kwargs) -> None:
        """Delete the configuration item."""
        pass

    def apply(self, fn):
        """Apply a function on this configuration item."""
        value = self.get()
        result = fn(value)
        self.set(result)
        return result


class Configuration(Configured, metaclass=abc.ABCMeta):
    """A configzen configuration object protocol."""

    @abc.abstractmethod
    def update(self, *args, **kwargs) -> None:
        """Update the configuration with new values."""
        pass

    @abc.abstractmethod
    def save(self, *args, **kwargs) -> None:
        """Save the configuration."""
        pass

    @abc.abstractmethod
    def load(self, *args, **kwargs) -> 'Configuration':
        """Load the configuration."""
        pass
