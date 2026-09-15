package main

import "fmt"

type Service struct{ Name string }

func (s Service) Start(port int) error {
	fmt.Println(port)
	return nil
}

func main() {
	s := Service{Name: "x"}
	s.Start(8080)
}
