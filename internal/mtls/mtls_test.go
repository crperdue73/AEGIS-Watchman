package mtls

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// testPKI generates a tiny CA + one leaf cert signed by it, written to PEM
// files. Enough to exercise a real TLS handshake in tests.
type testPKI struct {
	dir      string
	caCert   string
	caKey    string
	leafCert string
	leafKey  string
	pool     *x509.CertPool
}

func newTestPKI(t *testing.T) *testPKI {
	t.Helper()
	dir := t.TempDir()

	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
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

	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leafTmpl := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "localhost"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		DNSNames:     []string{"localhost"},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leafTmpl, caTmpl, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}

	p := &testPKI{dir: dir}
	p.caCert = p.write(t, "ca.crt", "CERTIFICATE", caDER)
	p.caKey = p.writeKey(t, "ca.key", caKey)
	p.leafCert = p.write(t, "leaf.crt", "CERTIFICATE", leafDER)
	p.leafKey = p.writeKey(t, "leaf.key", leafKey)

	p.pool = x509.NewCertPool()
	p.pool.AddCert(mustParse(t, caDER))
	return p
}

func (p *testPKI) write(t *testing.T, name, typ string, der []byte) string {
	t.Helper()
	path := filepath.Join(p.dir, name)
	blk := pem.EncodeToMemory(&pem.Block{Type: typ, Bytes: der})
	if err := os.WriteFile(path, blk, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func (p *testPKI) writeKey(t *testing.T, name string, key *ecdsa.PrivateKey) string {
	t.Helper()
	der, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return p.write(t, name, "EC PRIVATE KEY", der)
}

func mustParse(t *testing.T, der []byte) *x509.Certificate {
	t.Helper()
	c, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestValidate(t *testing.T) {
	cases := []struct {
		name    string
		cfg     Config
		wantErr bool
	}{
		{"empty is valid (tls off)", Config{}, false},
		{"cert without key", Config{CertFile: "c", CAFile: "ca"}, true},
		{"key without cert", Config{KeyFile: "k", CAFile: "ca"}, true},
		{"no ca and not insecure", Config{CertFile: "c", KeyFile: "k"}, true},
		{"complete", Config{CertFile: "c", KeyFile: "k", CAFile: "ca"}, false},
		{"insecure without ca allowed", Config{CertFile: "c", KeyFile: "k", InsecureSkipVerify: true}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.cfg.Validate()
			if (err != nil) != tc.wantErr {
				t.Errorf("Validate() err=%v, wantErr=%v", err, tc.wantErr)
			}
		})
	}
}

func TestEnabled(t *testing.T) {
	if (Config{}).Enabled() {
		t.Error("empty config should not be enabled")
	}
	if !(Config{CAFile: "x"}).Enabled() {
		t.Error("any material should enable")
	}
}

func TestClientAndServerHandshake(t *testing.T) {
	p := newTestPKI(t)

	serverCfg, err := Config{CertFile: p.leafCert, KeyFile: p.leafKey, CAFile: p.caCert}.ServerConfig()
	if err != nil {
		t.Fatalf("server config: %v", err)
	}
	if serverCfg.ClientAuth != tls.RequireAndVerifyClientCert {
		t.Error("server must require and verify client certs")
	}
	if serverCfg.MinVersion != tls.VersionTLS12 {
		t.Error("server MinVersion should be TLS 1.2")
	}

	clientCfg, err := Config{
		CertFile:   p.leafCert,
		KeyFile:    p.leafKey,
		CAFile:     p.caCert,
		ServerName: "localhost",
	}.ClientConfig()
	if err != nil {
		t.Fatalf("client config: %v", err)
	}
	if len(clientCfg.Certificates) != 1 {
		t.Error("client must present a certificate")
	}
	if clientCfg.MinVersion != tls.VersionTLS12 {
		t.Error("client MinVersion should be TLS 1.2")
	}

	// A real handshake over a loopback listener, then a byte exchange.
	ln, err := tls.Listen("tcp", "127.0.0.1:0", serverCfg)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()

	serverErr := make(chan error, 1)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			serverErr <- err
			return
		}
		defer conn.Close()
		buf := make([]byte, 5)
		if _, err := conn.Read(buf); err != nil {
			serverErr <- err
			return
		}
		if string(buf) != "hello" {
			serverErr <- err
			return
		}
		_, err = conn.Write([]byte("world"))
		serverErr <- err
	}()

	dialer := &tls.Dialer{Config: clientCfg}
	conn, err := dialer.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatalf("client dial (mutual TLS handshake) failed: %v", err)
	}
	defer conn.Close()

	if _, err := conn.Write([]byte("hello")); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 5)
	if _, err := conn.Read(buf); err != nil {
		t.Fatal(err)
	}
	if string(buf) != "world" {
		t.Errorf("got %q, want world", buf)
	}
	if err := <-serverErr; err != nil {
		t.Fatalf("server side: %v", err)
	}
}

func TestServerRejectsUntrustedClient(t *testing.T) {
	p := newTestPKI(t)
	serverCfg, err := Config{CertFile: p.leafCert, KeyFile: p.leafKey, CAFile: p.caCert}.ServerConfig()
	if err != nil {
		t.Fatal(err)
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:0", serverCfg)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		if conn, err := ln.Accept(); err == nil {
			conn.Close()
		}
	}()

	// A stranger PKI: its own CA and leaf. The server does not trust it.
	stranger := newTestPKI(t)
	strangerCfg, err := Config{
		CertFile:   stranger.leafCert,
		KeyFile:    stranger.leafKey,
		CAFile:     stranger.caCert, // trusts its own CA, not the server's
		ServerName: "localhost",
	}.ClientConfig()
	if err != nil {
		t.Fatal(err)
	}
	dialer := &tls.Dialer{Config: strangerCfg}
	if conn, err := dialer.Dial("tcp", ln.Addr().String()); err == nil {
		conn.Close()
		t.Fatal("handshake should have failed: untrusted client cert was accepted")
	}
}

func TestNoClientCertIsRejected(t *testing.T) {
	p := newTestPKI(t)
	serverCfg, err := Config{CertFile: p.leafCert, KeyFile: p.leafKey, CAFile: p.caCert}.ServerConfig()
	if err != nil {
		t.Fatal(err)
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:0", serverCfg)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		if conn, err := ln.Accept(); err == nil {
			conn.Close()
		}
	}()

	// Client trusts the server's CA but presents NO client certificate.
	dialer := &tls.Dialer{Config: &tls.Config{RootCAs: p.pool, ServerName: "localhost"}}
	if conn, err := dialer.Dial("tcp", ln.Addr().String()); err == nil {
		conn.Close()
		t.Fatal("handshake should have failed: missing client cert was accepted")
	}
}

func TestLoadKeyPairMissingFileErrors(t *testing.T) {
	cfg := Config{CertFile: "/nope/cert.pem", KeyFile: "/nope/key.pem", CAFile: "/nope/ca.pem"}
	if _, err := cfg.ClientConfig(); err == nil {
		t.Error("expected error for missing cert files")
	}
}

func TestBadCAFileErrors(t *testing.T) {
	p := newTestPKI(t)
	badCA := filepath.Join(p.dir, "bad-ca.pem")
	if err := os.WriteFile(badCA, []byte("not a certificate"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := Config{CertFile: p.leafCert, KeyFile: p.leafKey, CAFile: badCA}
	if _, err := cfg.ClientConfig(); err == nil {
		t.Error("expected error for a CA file with no valid PEM certs")
	}
}
