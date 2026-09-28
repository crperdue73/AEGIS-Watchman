package central

import (
	"context"
	"net/http"
	"time"
)

// contextWithTimeout returns the request's context with an added deadline.
// Extracted so the HTTP handler stays testable.
func contextWithTimeout(r *http.Request, d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(r.Context(), d)
}
