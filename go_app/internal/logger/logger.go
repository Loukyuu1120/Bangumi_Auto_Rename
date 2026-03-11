package logger

import (
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Level represents a log severity level.
type Level int

const (
	DEBUG Level = iota
	INFO
	WARNING
	ERROR
)

func (l Level) String() string {
	switch l {
	case DEBUG:
		return "DEBUG"
	case INFO:
		return "INFO"
	case WARNING:
		return "WARNING"
	case ERROR:
		return "ERROR"
	default:
		return "UNKNOWN"
	}
}

func ParseLevel(s string) Level {
	switch strings.ToUpper(strings.TrimSpace(s)) {
	case "DEBUG":
		return DEBUG
	case "WARNING", "WARN":
		return WARNING
	case "ERROR":
		return ERROR
	default:
		return INFO
	}
}

// Entry is a single log record kept in the in-memory ring buffer.
type Entry struct {
	Time    time.Time
	Level   Level
	Message string
}

func (e Entry) Format() string {
	return fmt.Sprintf("[%s] %s | %s", e.Level.String(), e.Time.Format("15:04:05"), e.Message)
}

// Logger is a levelled, multi-output logger with an in-memory history ring.
type Logger struct {
	mu       sync.RWMutex
	level    Level
	history  []Entry // circular buffer
	histSize int
	histPos  int
	histLen  int

	fileLogger    *log.Logger
	consoleLogger *log.Logger
	fileHandle    *os.File
}

var (
	global *Logger
	once   sync.Once
)

// Init initialises the global logger.  logDir is the directory for the rotating
// log file.  logLevel is the minimum severity to emit ("DEBUG", "INFO", etc.).
func Init(logDir string, logLevel string) error {
	var initErr error
	once.Do(func() {
		if err := os.MkdirAll(logDir, 0o755); err != nil {
			initErr = fmt.Errorf("creating log dir: %w", err)
			return
		}

		logFile := filepath.Join(logDir, "BAR.log")
		fh, err := os.OpenFile(logFile, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			initErr = fmt.Errorf("opening log file: %w", err)
			return
		}

		level := ParseLevel(logLevel)

		// Write to both file and stdout simultaneously.
		multiWriter := io.MultiWriter(os.Stdout, fh)

		global = &Logger{
			level:         level,
			history:       make([]Entry, 200),
			histSize:      200,
			fileLogger:    log.New(multiWriter, "", 0),
			consoleLogger: log.New(os.Stdout, "", 0),
			fileHandle:    fh,
		}
	})
	return initErr
}

// Get returns the global logger (panics if Init has not been called).
func Get() *Logger {
	if global == nil {
		// Fallback: initialise with stdout only so callers never get a nil panic.
		_ = Init(os.TempDir(), "INFO")
	}
	return global
}

// SetLevel changes the minimum log level at runtime.
func (l *Logger) SetLevel(level Level) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.level = level
}

func (l *Logger) log(level Level, format string, args ...interface{}) {
	l.mu.Lock()
	defer l.mu.Unlock()

	if level < l.level {
		return
	}

	msg := format
	if len(args) > 0 {
		msg = fmt.Sprintf(format, args...)
	}

	now := time.Now()
	line := fmt.Sprintf("[%s] %s | %s", level.String(), now.Format("2006-01-02 15:04:05"), msg)

	if l.fileLogger != nil {
		l.fileLogger.Println(line)
	}

	// Store in ring buffer
	entry := Entry{Time: now, Level: level, Message: msg}
	l.history[l.histPos] = entry
	l.histPos = (l.histPos + 1) % l.histSize
	if l.histLen < l.histSize {
		l.histLen++
	}
}

// Debug logs at DEBUG level.
func (l *Logger) Debug(format string, args ...interface{}) {
	l.log(DEBUG, format, args...)
}

// Info logs at INFO level.
func (l *Logger) Info(format string, args ...interface{}) {
	l.log(INFO, format, args...)
}

// Warn logs at WARNING level.
func (l *Logger) Warn(format string, args ...interface{}) {
	l.log(WARNING, format, args...)
}

// Error logs at ERROR level.
func (l *Logger) Error(format string, args ...interface{}) {
	l.log(ERROR, format, args...)
}

// History returns up to n most-recent log entries (newest last).
func (l *Logger) History(n int) []Entry {
	l.mu.RLock()
	defer l.mu.RUnlock()

	if n <= 0 || l.histLen == 0 {
		return nil
	}
	if n > l.histLen {
		n = l.histLen
	}

	result := make([]Entry, n)
	// Oldest entry in the ring is at histPos when the ring is full,
	// or at 0 when it isn't full yet.
	start := 0
	if l.histLen == l.histSize {
		start = l.histPos
	}

	for i := 0; i < n; i++ {
		// We want the last n entries, so skip the earliest (histLen-n) ones.
		idx := (start + (l.histLen - n) + i) % l.histSize
		result[i] = l.history[idx]
	}
	return result
}

// HistoryFormatted returns the last n entries as formatted strings.
func (l *Logger) HistoryFormatted(n int) []string {
	entries := l.History(n)
	out := make([]string, len(entries))
	for i, e := range entries {
		out[i] = e.Format()
	}
	return out
}

// Close flushes and closes the underlying log file.
func (l *Logger) Close() {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.fileHandle != nil {
		_ = l.fileHandle.Close()
		l.fileHandle = nil
	}
}

// --- Package-level convenience wrappers around the global logger ---

func Debug(format string, args ...interface{}) { Get().Debug(format, args...) }
func Info(format string, args ...interface{})  { Get().Info(format, args...) }
func Warn(format string, args ...interface{})  { Get().Warn(format, args...) }
func Error(format string, args ...interface{}) { Get().Error(format, args...) }
