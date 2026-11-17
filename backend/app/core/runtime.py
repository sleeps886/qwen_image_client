"""Process-local concurrency budget shared by recognition and evaluation."""
import threading

SLOTS=threading.BoundedSemaphore(2)
