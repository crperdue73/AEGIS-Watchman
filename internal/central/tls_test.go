package central

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/mtls"
)

// TestHTTPSIngestOverMutualTLS exercises the real HTTP handler behind a real
// mTLS listener using the real collector client — the whole ingest path, not a
// stub. This is the test that proves the encrypted hop actually carries drift.
func TestHTTPSIngestOverMutualTLS(t *testing.T) {
	pki := newTestPKI(t)

	serverTLS, err := pki.serverCfg().ServerConfig()
	if err != nil {
		t.Fatalf("server tls: %v", err)
	}

	svc := NewService(NewMemoryStore())
	srv := &http.Server{Handler: svc.Handler(), TLSConfig: serverTLS}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	tlsLn := tls.NewListener(ln, serverTLS)
	go func() { _ = srv.Serve(tlsLn) }()
	defer srv.Close()

	endpoint := "https://" + ln.Addr().String() + "/api/v1/events"
	client, err := collector.NewTLSClient(endpoint, pki.clientCfg())
	if err != nil {
		t.Fatalf("collector tls client: %v", err)
	}
	if !client.TLS {
		t.Error("client should report TLS enabled")
	}

	ctx := context.Background()
	mkInv := func(sum string) *collector.Inventory {
		return &collector.Inventory{
			Root:  "/home/x/.openclaw/workspace-x",
			Files: []collector.FileState{{Path: "SOUL.md", SHA256: sum, Size: 10}},
		}
	}

	// Enrollment over mTLS: silent.
	first, err := client.Submit(ctx, collector.InventoryRequest{Host: host, Agent: agent, Inv: mkInv("aaa")})
	if err != nil {
		t.Fatalf("first submit over mTLS: %v", err)
	}
	if !first.FirstSeen {
		t.Error("expected first_seen on enrollment")
	}

	// Drift over mTLS: the CARBONATO edit, caught in transit.
	second, err := client.Submit(ctx, collector.InventoryRequest{Host: host, Agent: agent, Inv: mkInv("bbb")})
	if err != nil {
		t.Fatalf("second submit over mTLS: %v", err)
	}
	if len(second.Events) != 1 {
		t.Fatalf("expected 1 drift event over mTLS, got %d", len(second.Events))
	}
	if second.Events[0].File == nil || second.Events[0].File.Path != "SOUL.md" {
		t.Errorf("drift event lost file detail: %+v", second.Events[0])
	}
}

// --- test PKI helpers (kept local to central; the mtls package has its own) ---

type testPKI struct {
	dir      string
	caCert   string
	leafCert string
	leafKey  string
}

func newTestPKI(t *testing.T) *testPKI {
	t.Helper()
	dir := t.TempDir()

	caKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	caTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "watchman-test-ca"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTmpl, caTmpl, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}

	leafKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	leafTmpl := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "localhost"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
		DNSNames:     []string{"localhost"},
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leafTmpl, caTmpl, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	leafKeyDER, _ := x509.MarshalECPrivateKey(leafKey)

	p := &testPKI{dir: dir}
	p.caCert = writePEM(t, dir, "ca.crt", "CERTIFICATE", caDER)
	p.leafCert = writePEM(t, dir, "leaf.crt", "CERTIFICATE", leafDER)
	p.leafKey = writePEM(t, dir, "leaf.key", "EC PRIVATE KEY", leafKeyDER)
	return p
}

func writePEM(t *testing.T, dir, name, typ string, der []byte) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: typ, Bytes: der}), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func (p *testPKI) serverCfg() mtls.Config {
	return mtls.Config{CertFile: p.leafCert, KeyFile: p.leafKey, CAFile: p.caCert}
}

func (p *testPKI) clientCfg() mtls.Config {
	return mtls.Config{CertFile: p.leafCert, KeyFile: p.leafKey, CAFile: p.caCert, ServerName: "localhost"}
}
